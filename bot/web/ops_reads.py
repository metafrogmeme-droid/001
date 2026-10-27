"""The API bridge's read routes: /parity, /attestation, /redteam.

Each one answers a question an operator asks of the bot, and each is held to the
same three rules as the telemetry stream beside it (`telemetry_stream`):

  * DOLLAR-FREE. These are token-gated, but they are also the payloads a
    dashboard forwards, so they publish percent, ratio, count and verdict and
    nothing in money. `parity_summary` carries net, gross, fees and notional in
    dollars, and `parity_verdict`'s sentences print the mean net per trade in
    dollars, so the parity payload is built by WHITELIST from the summary: a
    key added to the summary tomorrow does not reach the route until someone
    adds it here.
  * UNREADABLE IS NEVER ZERO. A closed-trade file that is there and will not
    parse is `state: "unreadable"` (served 503), not a parity report over zero
    trades; a missing key file is `absent`, not an empty public key; an audit
    chain this process cannot verify is `unreadable` or `too_large`, never
    `intact`. `bot.backtest.parity.load_closed_trades` answers `[]` for all of
    those by design ("fail-soft, a reporting tool"), which is why this module
    reads the file itself.
  * ERROR TEXT NAMES THE EXCEPTION CLASS, never its message.

`/attestation` reports the audit-attestation PUBLIC key and the verdict of
re-deriving the audit chain. It does not sign, and it does not change how the
chain starts or links. The key is the Ed25519 key that signs audit batches
(`bot.utils.attestation`), not a venue or chain key; the public half is derived
from the seed on disk without generating one when none exists, because a
read route that creates the key it was asked about would be a writer.

`/redteam` runs `scripts/red_team.py --json` in a child process and summarises
it. A child, because the red team drives a real `RiskEngine` through thirty
adversarial ideas and the engine narrates every check to the risk audit log:
run in this process, a status read would write thirty synthetic rejections into
the same log the telemetry stream tails as the bot's alerts. The child gets the
environment minus anything named like a credential. The answer is cached, and a
lock admits one run at a time, so the route cannot be used to fork-bomb the
host.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from bot.utils.secret_shapes import scrub_secrets

REPO_ROOT = Path(__file__).resolve().parents[2]

# ── /parity ──────────────────────────────────────────────────────────────────

#: The headline keys `parity_summary` returns that are counts, ratios or words.
#: Every other key (net, gross, fees, notional, the per-group nets) is money.
_PARITY_KEYS = (
    "trades", "excluded_non_fills", "win_rate", "wins", "losses",
    "flat", "pf", "fees_read", "fees_stated", "fees_close_leg", "fees_estimated",
    "fees_basis_unrecorded", "min_fee_sample", "realized_fee_rate",
    "modeled_fee_rate", "fee_vs_model", "fee_drag_of_gross", "inferred_fills",
)
#: The verdict's figures. Its `*_sentence` fields are NOT here: the edge
#: sentence prints the mean net per trade in dollars.
_VERDICT_KEYS = (
    "n", "inferred", "min_trades", "edge", "ballpark", "in_universe", "outside",
    "live_win_rate", "live_wr_interval", "live_pf", "inferred_causes",
)
#: The benchmark on record, minus its dollar fields (`pooled_net_usd`,
#: `pooled_mean_net_usd`) and its filesystem path.
_BENCHMARK_KEYS = (
    "state", "dataset", "dataset_hash", "recorded_at", "code_sha", "folds_run",
    "profitable_folds", "pooled_trades", "pooled_wins", "pooled_losses",
    "pooled_win_rate", "pooled_pf", "universe", "leverage", "leverage_recorded",
    "mean_oos_return_pct",
)


def read_closed_trades(path: "str | os.PathLike[str]") -> "tuple[str, list[dict]]":
    """(state, rows) where state is "read", "absent" or "unreadable".

    The three are different facts. No file is a bot that has closed nothing (or
    never written one); a file that does not parse, or parses to something that
    is not a list of records, is a record this process could not read."""
    p = Path(path)
    try:
        if not p.exists():
            return "absent", []
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return "unreadable", []
    if isinstance(data, dict):
        data = data.get("closed") if data.get("closed") is not None else data.get("trades")
    if not isinstance(data, list):
        return "unreadable", []
    return "read", [t for t in data if isinstance(t, dict)]


def _pick(src: dict, keys: tuple[str, ...]) -> dict:
    return {k: src[k] for k in keys if k in src}


def parity_payload(trades_state: str, trades: list[dict], commission_pct: float,
                   benchmark: Any = None) -> dict:
    """The /parity body: live realized performance against the benchmark on
    record, in counts, ratios and verdict words.

    ``trades_state`` is `read_closed_trades`' first value. "absent" and
    "unreadable" return a body with no figures and say which."""
    out: dict[str, Any] = {"state": trades_state, "as_of": _now_iso()}
    if trades_state != "read":
        return out
    from bot.backtest.parity import parity_summary

    summary = parity_summary(trades, commission_pct, benchmark)
    out.update(_pick(summary, _PARITY_KEYS))
    if "unscored_pnl" in summary:
        # A COUNT of closes with no P&L on record. Renamed on the way out: a
        # key spelled `*_pnl` is the first thing a dollar-free check flags.
        out["unscored_closes"] = summary["unscored_pnl"]
    aborts = summary.get("aborts")
    if isinstance(aborts, dict) and "trades" in aborts:
        out["execution_aborts"] = aborts["trades"]
    verdict = summary.get("verdict")
    if isinstance(verdict, dict):
        out["verdict"] = _pick(verdict, _VERDICT_KEYS)
    bench = summary.get("benchmark")
    if isinstance(bench, dict):
        out["benchmark"] = _pick(bench, _BENCHMARK_KEYS)
    return _json_safe(out)


def _json_safe(value: Any) -> Any:
    """``value`` with non-finite floats removed and tuples as lists, so the
    body is valid JSON. A non-finite figure is dropped, not zeroed."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


# ── /attestation ─────────────────────────────────────────────────────────────

#: A log larger than this is not verified inside a request: re-deriving every
#: hash is linear in the file and this runs on a request thread.
CHAIN_VERIFY_MAX_BYTES = 128 * 1024 * 1024
_MAX_PROBLEMS = 10
_MAX_PROBLEM_CHARS = 200

_chain_cache: dict[str, tuple[tuple[int, int], dict]] = {}


def read_public_key(key_path: "str | os.PathLike[str]") -> dict:
    """The attestation PUBLIC key, derived from the seed on disk.

    Never creates the key (`AttestationEngine()` does, and a read route must
    not), and never returns, logs or formats the seed. States: "present",
    "absent", "unreadable" (the file is there and is not a 32-byte seed) and
    "unavailable" (the `cryptography` package is not installed)."""
    p = Path(key_path)
    base = {"algorithm": "ed25519", "public_key_hex": None}
    try:
        if not p.exists():
            return {**base, "state": "absent"}
        seed = p.read_bytes()[:32]
    except OSError:
        return {**base, "state": "unreadable"}
    if len(seed) != 32:
        return {**base, "state": "unreadable"}
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
        return {**base, "state": "unavailable"}
    try:
        public = Ed25519PrivateKey.from_private_bytes(seed).public_key()
        raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except Exception:  # noqa: BLE001 - the class is not worth a seed-adjacent message
        return {**base, "state": "unreadable"}
    return {**base, "state": "present", "public_key_hex": raw.hex()}


def verify_chain(chain: Any) -> dict:
    """Re-derive the audit chain and say what was found.

    States: "intact", "broken" (with up to ten problems, scrubbed), "absent"
    (no log yet), "too_large" (not verified here) and "unreadable". The verdict
    is cached on (size, mtime) so repeated reads of an unchanged log re-derive
    nothing; any append, and any edit, changes the key."""
    path = Path(chain.path)
    try:
        st = path.stat()
    except FileNotFoundError:
        return {"state": "absent", "entries": 0}
    except OSError:
        return {"state": "unreadable"}
    if st.st_size > CHAIN_VERIFY_MAX_BYTES:
        return {"state": "too_large", "bytes": st.st_size}
    key = (st.st_size, st.st_mtime_ns)
    cached = _chain_cache.get(str(path))
    if cached is not None and cached[0] == key:
        return dict(cached[1])
    try:
        ok, problems = chain.verify(str(path))
        entries = chain.get_chain_length()
        tail = chain.get_entries(limit=1)
    except Exception:  # noqa: BLE001
        return {"state": "unreadable"}
    result: dict[str, Any] = {"state": "intact" if ok else "broken", "entries": entries}
    if tail:
        result["tip"] = {"sequence": tail[-1].sequence, "entry_hash": tail[-1].entry_hash}
    if not ok:
        result["problem_count"] = len(problems)
        result["problems"] = [scrub_secrets(str(p))[:_MAX_PROBLEM_CHARS]
                              for p in problems[:_MAX_PROBLEMS]]
    _chain_cache[str(path)] = (key, dict(result))
    if len(_chain_cache) > 8:
        _chain_cache.pop(next(iter(_chain_cache)))
    return result


def attestation_payload(chain: Any, key_path: "str | os.PathLike[str] | None" = None) -> dict:
    """The /attestation body: the public key and the chain verdict. The default
    key path is the one `AttestationEngine` signs with, read from where it is
    defined rather than spelled a second time."""
    if key_path is None:
        from bot.utils.attestation import _KEY_PATH
        key_path = _KEY_PATH
    return {"as_of": _now_iso(), "key": read_public_key(key_path), "chain": verify_chain(chain)}


# ── /redteam ─────────────────────────────────────────────────────────────────

RED_TEAM_SCRIPT = REPO_ROOT / "scripts" / "red_team.py"
RED_TEAM_TIMEOUT_S = 120.0
#: A good answer is reused this long. A run that could not complete is reused
#: for a much shorter time, so a transient fault is retried soon but not hammered.
RED_TEAM_TTL_S = 300.0
RED_TEAM_FAILURE_TTL_S = 30.0

_SECRET_ENV_RE = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|PASSPHRASE|CREDENTIAL|CREDS)", re.IGNORECASE)

_red_team_lock: Optional[asyncio.Lock] = None
_red_team_cache: Optional[tuple[float, dict]] = None


def child_env(env: "dict[str, str] | None" = None) -> dict[str, str]:
    """``env`` (default: this process's) without any variable named like a
    credential. The red team needs none of them, and a child that never held the
    dashboard token cannot print it."""
    source = os.environ if env is None else env
    return {k: v for k, v in source.items() if not _SECRET_ENV_RE.search(k)}


def summarize_red_team(report: dict) -> dict:
    """The /redteam body from `StressTestReport`'s JSON: counts, rates and the
    NAMES of anything that got through. The scenarios' ideas are not forwarded:
    they hold entry prices."""
    scenarios = report.get("scenarios")
    if not isinstance(scenarios, list):
        return {"ran": False, "error": "MalformedReport"}
    by_category: dict[str, dict[str, int]] = {}
    slipped: list[str] = []
    for s in scenarios:
        if not isinstance(s, dict):
            continue
        row = by_category.setdefault(str(s.get("category") or "unknown"),
                                     {"passed": 0, "failed": 0})
        if s.get("passed") is True:
            row["passed"] += 1
        else:
            row["failed"] += 1
            slipped.append(str(s.get("name") or "unnamed")[:80])
    total = sum(r["passed"] + r["failed"] for r in by_category.values())
    passed = sum(r["passed"] for r in by_category.values())
    return {
        "ran": True,
        "total_scenarios": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total * 100.0, 1) if total else None,
        "by_category": by_category,
        "failed_scenarios": slipped[:20],
    }


async def _run_red_team_child(timeout_s: float) -> dict:
    """Run the script and parse its JSON. Never raises: a run that could not
    complete is `{"ran": false, "error": <class>}`, which is NOT a pass."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(RED_TEAM_SCRIPT), "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=str(REPO_ROOT), env=child_env())
    except Exception as exc:  # noqa: BLE001
        return {"ran": False, "error": type(exc).__name__}
    try:
        async with asyncio.timeout(timeout_s):
            out, _ = await proc.communicate()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return {"ran": False, "error": "TimeoutError"}
    except Exception as exc:  # noqa: BLE001
        return {"ran": False, "error": type(exc).__name__}
    # Exit 1 means a scenario got through, which is an answer; so does 0. The
    # report is the last JSON line (the engine may print above it).
    lines = [ln for ln in out.decode("utf-8", "replace").splitlines() if ln.strip()]
    try:
        report = json.loads(lines[-1]) if lines else None
    except ValueError:
        report = None
    if not isinstance(report, dict) or report.get("ran") is False:
        return {"ran": False, "error": "HarnessError"}
    return summarize_red_team(report)


async def red_team_payload(
    runner: Optional[Callable[[float], Any]] = None,
    *,
    ttl_s: float = RED_TEAM_TTL_S,
    failure_ttl_s: float = RED_TEAM_FAILURE_TTL_S,
    timeout_s: float = RED_TEAM_TIMEOUT_S,
) -> dict:
    """The /redteam body, from cache when fresh, otherwise from one run.

    ``runner`` exists so a test never starts a child; it is awaited with the
    timeout and must return the same shape `_run_red_team_child` does."""
    global _red_team_lock, _red_team_cache
    if _red_team_lock is None:
        _red_team_lock = asyncio.Lock()
    async with _red_team_lock:
        now = time.monotonic()
        if _red_team_cache is not None:
            at, body = _red_team_cache
            limit = ttl_s if body.get("ran") else failure_ttl_s
            if now - at < limit:
                return {**body, "cached": True}
        body = await (runner or _run_red_team_child)(timeout_s)
        body = {**body, "as_of": _now_iso()}
        _red_team_cache = (time.monotonic(), body)
        return {**body, "cached": False}


def _reset_red_team_cache() -> None:
    """Forget the cached answer and the lock (a test's, or a loop change's)."""
    global _red_team_lock, _red_team_cache
    _red_team_lock = None
    _red_team_cache = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
