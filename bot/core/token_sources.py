"""Where the detective's facts come from, and who supplied each one.

The four scorers — ``token_safety``, ``deployer_history``, ``token_dossier``,
``presale_claims`` — are pure and already honest: a feature nobody supplied
becomes an ``unknown`` check, and ``coverage()`` counts it so a verdict drawn
from two readings cannot pass for one drawn from twelve.

None of that helps while nothing feeds them. This is the layer that does, and
it exists mainly to keep three failures apart that a naive fetcher collapses
into one empty dict:

    unavailable   we never asked — no API key, source disabled
    error         we asked and the asking failed — timeout, 500, bad JSON
    empty         we asked, it answered, and it knows nothing about this token

Only the third is information about the token. The first two are information
about US, and a scanner that reports "no honeypot flag" because its own key was
missing has published a safety claim it never made. `available()` is checked
BEFORE the call for exactly this reason: a paid source with no key is skipped
and named as skipped, so coverage says "we asked four of six" rather than
scoring the token on four and implying six.

PROVENANCE, NOT JUST VALUES

Every field carries the name of the source that supplied it. `token_dossier`
already accepts a `sources` map and prints "via <source>" per section; it had
nothing to put there. A reader who cannot tell which source claimed a token is
a honeypot cannot weigh the claim.

DISAGREEMENT IS A FINDING

When two sources supply the same field with different values, the first wins
and the conflict is RECORDED. It is tempting to treat that as noise to be
resolved silently, but two independent readers disagreeing about whether a
contract can be sold is exactly the thing a detective should surface — and
averaging them, or taking the friendlier one, is how a rug gets a clean bill.

NO NETWORK IN HERE

`gather` takes the sources it is handed. Every real fetcher keeps its transport
injectable, so the honesty properties are tested against fakes and CI never
depends on a third party being up — which would also make a red build mean
"someone else's API is down" rather than "this is broken".
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

# What happened when we went looking. Distinct words on purpose — see module
# docstring; collapsing any two of them loses the distinction the layer exists
# to keep.
OK = "ok"                    # answered, and told us something
EMPTY = "empty"              # answered, knows nothing about this token
UNAVAILABLE = "unavailable"  # never asked — no key, disabled
ERROR = "error"              # asked, and the asking failed

#: Statuses meaning "this source contributed no reading". Note EMPTY is in here
#: too: an answer of "I don't know this token" adds no feature. What separates
#: it from the others is that it is a fact about the TOKEN, and it is reported
#: as such rather than as a gap in our diligence.
_NO_READING = (EMPTY, UNAVAILABLE, ERROR)


def _result(name: str, status: str, features: Optional[dict] = None,
            detail: str = "") -> dict:
    return {"source": name, "status": status,
            "features": dict(features or {}), "detail": detail}


async def _run_one(source: Any, chain: str, address: str, timeout: float) -> dict:
    """Call one source, converting every failure into a NAMED non-reading."""
    name = getattr(source, "name", source.__class__.__name__)
    try:
        if not source.available():
            # Checked BEFORE the call. A paid source with no key must be
            # "we never asked", never "we asked and learned nothing".
            return _result(name, UNAVAILABLE, detail="not configured")
    except Exception as exc:                                  # noqa: BLE001
        return _result(name, ERROR, detail=f"availability check failed: {exc!s}"[:120])

    try:
        got = await asyncio.wait_for(source.fetch(chain, address), timeout)
    except asyncio.TimeoutError:
        logger.warning("token source %s timed out after %.1fs", name, timeout)
        return _result(name, ERROR, detail=f"timeout after {timeout:g}s")
    except Exception as exc:                                  # noqa: BLE001
        logger.warning("token source %s failed: %s", name, exc)
        return _result(name, ERROR, detail=str(exc)[:120])

    if not got:
        return _result(name, EMPTY, detail="no record for this token")
    # A source may legitimately return a key it looked at and could not read.
    # Those are dropped: `assess_token` treats a missing feature as unknown,
    # and carrying None through would make the provenance map claim this source
    # supplied something it did not.
    usable = {k: v for k, v in dict(got).items() if v is not None}
    if not usable:
        return _result(name, EMPTY, detail="answered with nothing readable")
    return _result(name, OK, features=usable)


async def gather(sources: Optional[Sequence], chain: str, address: str,
                 timeout: float = 8.0) -> dict:
    """Ask every source about one token. Returns::

        {features, provenance, conflicts, results,
         asked, answered, unavailable, errored}

    `features` holds only what somebody actually read — a field no source
    supplied is ABSENT, not None and not zero, so the scorers see `unknown` and
    `coverage()` counts it honestly.
    """
    srcs = list(sources or [])
    if not srcs:
        return {"features": {}, "provenance": {}, "conflicts": [], "results": [],
                "asked": 0, "answered": 0, "unavailable": 0, "errored": 0}

    results = await asyncio.gather(
        *(_run_one(s, chain, address, timeout) for s in srcs))

    features: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    conflicts: list[dict] = []
    for r in results:
        for key, value in r["features"].items():
            if key not in features:
                features[key] = value
                provenance[key] = r["source"]
                continue
            if features[key] != value:
                # First writer keeps the field; the disagreement is published.
                # Silently preferring either one would let a friendlier source
                # overwrite a warning, which is the whole risk of blending feeds.
                conflicts.append({
                    "field": key,
                    "kept": {"source": provenance[key], "value": features[key]},
                    "rejected": {"source": r["source"], "value": value},
                })

    return {
        "features": features,
        "provenance": provenance,
        "conflicts": conflicts,
        "results": results,
        "asked": len(srcs),
        "answered": sum(1 for r in results if r["status"] == OK),
        "unavailable": sum(1 for r in results if r["status"] == UNAVAILABLE),
        "errored": sum(1 for r in results if r["status"] == ERROR),
    }


def human_readable(g: Optional[dict]) -> str:
    """Plain-text render of where the facts came from (no markup)."""
    if not g or not isinstance(g, dict):
        return "No source report."
    lines = [f"SOURCES: {g.get('answered', 0)} of {g.get('asked', 0)} answered"]
    for r in g.get("results", []):
        if r["status"] == OK:
            lines.append(f"   ✓ {r['source']} — {len(r['features'])} field(s)")
        else:
            # A source that did not answer is LISTED, with which of the three
            # non-answers it was. Summarising them away is how "we could not
            # look" starts reading as "there was nothing to find".
            lines.append(f"   · {r['source']} — {r['status']}"
                         + (f": {r['detail']}" if r["detail"] else ""))
    for c in g.get("conflicts", []):
        lines.append(f"   ! {c['field']}: {c['kept']['source']} says "
                     f"{c['kept']['value']!r}, {c['rejected']['source']} says "
                     f"{c['rejected']['value']!r} — kept the first, "
                     "neither is confirmed")
    return "\n".join(lines)


# ── A free source, with its transport injectable ─────────────────────

class DexScreenerSource:
    """Pair data from dexscreener.com — free, no key, generous rate limit.

    Supplies liquidity, volume and pair age: the fields `meme_gate` already
    gates on. It says NOTHING about honeypots or contract permissions, and this
    class must never pretend otherwise — a token absent from DexScreener is a
    token with no indexed pair, which is a fact about liquidity and not a
    clean bill of health.

    `transport` is injected so the honesty properties are tested against fakes.
    A test that needed dexscreener to be up would fail for reasons that have
    nothing to do with this repository.
    """

    name = "dexscreener"
    requires_key = False

    def __init__(self, transport: Optional[Callable] = None) -> None:
        self._transport = transport

    def available(self) -> bool:
        return True

    async def _get(self, url: str) -> Optional[dict]:
        if self._transport is not None:
            return await self._transport(url)
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                return await resp.json(content_type=None)

    async def fetch(self, chain: str, address: str) -> dict:
        data = await self._get(
            f"https://api.dexscreener.com/latest/dex/tokens/{address}")
        pairs = (data or {}).get("pairs") or []
        if not pairs:
            return {}
        # Deepest pool: the one an exit would actually route through.
        best = max(pairs, key=lambda p: _f((p.get("liquidity") or {}).get("usd")) or 0.0)
        out: dict[str, Any] = {}
        liq = _f((best.get("liquidity") or {}).get("usd"))
        if liq is not None:
            out["liquidity_usd"] = liq
        vol = _f((best.get("volume") or {}).get("h24"))
        if vol is not None:
            out["volume_24h_usd"] = vol
        created = best.get("pairCreatedAt")
        if created is not None:
            ms = _f(created)
            if ms is not None:
                out["pair_created_at_ms"] = ms
        return out


def _f(x: Any) -> Optional[float]:
    """float(x) or None — never 0.0 for an unreadable value."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None      # NaN is not a reading
