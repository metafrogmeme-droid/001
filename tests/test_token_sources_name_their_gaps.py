"""Three ways to learn nothing, and only one of them is about the token.

The four scorers are pure and already honest — a feature nobody supplied
becomes `unknown`, and `coverage()` counts it. That protection is worth nothing
while the layer feeding them collapses every failure into an empty dict:

    unavailable   we never asked — no API key, source disabled
    error         we asked and the asking failed — timeout, 500, bad JSON
    empty         we asked, it answered, and it knows nothing about this token

Only `empty` is information about the TOKEN. The other two are information
about us, and a scanner reporting "no honeypot flag" because its own key was
missing has published a safety claim it never made.

RED HERRING, planted in test_an_empty_answer_is_not_a_clean_bill: a source that
answers successfully and knows nothing about the address. It is the most
reassuring-looking result in the set — a live source, a 200, no flags — and it
is the one that means least. A token absent from a pair indexer is a token with
no indexed pair, not a token that passed a check.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.core.token_sources import (EMPTY, ERROR, OK, UNAVAILABLE,
                                    DexScreenerSource, gather, human_readable)


class _Src:
    """A source that does whatever the test needs."""

    def __init__(self, name, features=None, *, avail=True, raises=None,
                 hang=False):
        self.name = name
        self._features = features
        self._avail = avail
        self._raises = raises
        self._hang = hang
        self.called = False

    def available(self):
        return self._avail

    async def fetch(self, chain, address):
        self.called = True
        if self._hang:
            await asyncio.sleep(10)
        if self._raises:
            raise self._raises
        return self._features


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── the three non-answers stay apart ─────────────────────────────────

@pytest.mark.asyncio
async def test_a_source_with_no_key_is_never_even_asked():
    """The paid-ready half. Calling a source that cannot answer and recording
    the failure as an error would put the blame in the wrong place — and
    reporting it as a reading would be worse."""
    s = _Src("paid-scanner", avail=False)
    g = await gather([s], "eth", "0xabc")
    assert s.called is False
    assert g["results"][0]["status"] == UNAVAILABLE
    assert g["unavailable"] == 1
    assert g["features"] == {}


@pytest.mark.asyncio
async def test_a_failing_source_is_an_error_not_an_absence():
    g = await gather([_Src("flaky", raises=RuntimeError("HTTP 503"))], "eth", "0x1")
    assert g["results"][0]["status"] == ERROR
    assert g["errored"] == 1
    assert g["features"] == {}


@pytest.mark.asyncio
async def test_a_timeout_is_an_error_not_an_absence():
    g = await gather([_Src("slow", hang=True)], "eth", "0x1", timeout=0.05)
    assert g["results"][0]["status"] == ERROR
    assert "timeout" in g["results"][0]["detail"]


@pytest.mark.asyncio
async def test_an_empty_answer_is_not_a_clean_bill():
    """RED HERRING. A live source, a clean 200, and no flags — the most
    reassuring result in the set and the one that means least."""
    g = await gather([_Src("indexer", features={})], "eth", "0x1")
    assert g["results"][0]["status"] == EMPTY
    assert g["features"] == {}
    assert g["answered"] == 0, "an empty answer is not an answer about the token"


@pytest.mark.asyncio
async def test_the_three_are_distinguishable_in_the_report():
    g = await gather([
        _Src("a", avail=False),
        _Src("b", raises=ValueError("bad json")),
        _Src("c", features={}),
        _Src("d", features={"honeypot": True}),
    ], "eth", "0x1")
    statuses = {r["source"]: r["status"] for r in g["results"]}
    assert statuses == {"a": UNAVAILABLE, "b": ERROR, "c": EMPTY, "d": OK}
    assert len({UNAVAILABLE, ERROR, EMPTY, OK}) == 4


@pytest.mark.asyncio
async def test_one_dead_source_does_not_lose_the_others():
    """The `omit` strategy from CLAUDE.md's table: catch each source
    individually so one dead feed cannot blank a composite view."""
    g = await gather([
        _Src("dead", raises=RuntimeError("down")),
        _Src("alive", features={"liquidity_usd": 42000.0}),
    ], "eth", "0x1")
    assert g["features"] == {"liquidity_usd": 42000.0}
    assert g["errored"] == 1 and g["answered"] == 1


# ── absent means absent ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_field_nobody_supplied_is_missing_not_zero():
    """The scorers key on `is None` / absence. A zero here would be a reading."""
    g = await gather([_Src("a", features={"liquidity_usd": 1.0})], "eth", "0x1")
    assert "honeypot" not in g["features"]
    assert "top_holder_pct" not in g["features"]


@pytest.mark.asyncio
async def test_a_none_valued_field_is_dropped_rather_than_attributed():
    """A source saying "I looked and could not tell" must not appear in the
    provenance map as having supplied the field."""
    g = await gather([_Src("a", features={"honeypot": None,
                                          "liquidity_usd": 5.0})], "eth", "0x1")
    assert "honeypot" not in g["features"]
    assert "honeypot" not in g["provenance"]
    assert g["provenance"]["liquidity_usd"] == "a"


@pytest.mark.asyncio
async def test_false_is_a_reading_and_survives():
    """`honeypot: False` is a measured negative — the exact value a `if not v`
    filter would silently discard."""
    g = await gather([_Src("a", features={"honeypot": False})], "eth", "0x1")
    assert g["features"]["honeypot"] is False


@pytest.mark.asyncio
async def test_zero_is_a_reading_and_survives():
    g = await gather([_Src("a", features={"liquidity_usd": 0.0})], "eth", "0x1")
    assert g["features"]["liquidity_usd"] == 0.0
    assert g["answered"] == 1


# ── provenance ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_every_field_names_its_source():
    """token_dossier prints "via <source>" per section and had nothing to put
    there. A reader who cannot tell who claimed a token is a honeypot cannot
    weigh the claim."""
    g = await gather([_Src("a", features={"honeypot": True}),
                      _Src("b", features={"liquidity_usd": 10.0})], "eth", "0x1")
    assert g["provenance"] == {"honeypot": "a", "liquidity_usd": "b"}


# ── disagreement is published ────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_sources_disagreeing_is_recorded_not_resolved():
    """Two independent readers disagreeing about whether a contract can be sold
    is the finding. Averaging them, or taking the friendlier one, is how a rug
    gets a clean bill."""
    g = await gather([_Src("a", features={"honeypot": True}),
                      _Src("b", features={"honeypot": False})], "eth", "0x1")
    assert g["features"]["honeypot"] is True, "first writer keeps the field"
    assert len(g["conflicts"]) == 1
    c = g["conflicts"][0]
    assert c["field"] == "honeypot"
    assert c["kept"]["source"] == "a" and c["rejected"]["source"] == "b"


@pytest.mark.asyncio
async def test_agreement_is_not_a_conflict():
    g = await gather([_Src("a", features={"honeypot": True}),
                      _Src("b", features={"honeypot": True})], "eth", "0x1")
    assert g["conflicts"] == []


@pytest.mark.asyncio
async def test_the_render_names_the_disagreement_and_confirms_neither():
    g = await gather([_Src("a", features={"honeypot": True}),
                      _Src("b", features={"honeypot": False})], "eth", "0x1")
    text = human_readable(g)
    assert "honeypot" in text
    assert "neither is confirmed" in text


@pytest.mark.asyncio
async def test_the_render_lists_the_sources_that_did_not_answer():
    """Summarising them away is how "we could not look" starts reading as
    "there was nothing to find"."""
    g = await gather([_Src("paid", avail=False),
                      _Src("ok", features={"liquidity_usd": 1.0})], "eth", "0x1")
    text = human_readable(g)
    assert "paid" in text and UNAVAILABLE in text


# ── it plugs into the scorers ────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_gather_with_no_readings_scores_as_no_coverage():
    """End to end: nothing read → the scanner must not produce a basis that
    implies it looked."""
    from bot.core.token_safety import assess_token, coverage
    g = await gather([_Src("a", avail=False)], "eth", "0x1")
    report = assess_token(g["features"])
    cov = coverage(report["checks"])
    assert cov["readable"] == 0
    assert cov["basis"] == "none"
    assert report["verdict"] != "safe", "a verdict of safe on nothing read"


@pytest.mark.asyncio
async def test_a_partial_gather_says_so():
    from bot.core.token_safety import assess_token, coverage
    g = await gather([_Src("a", features={"honeypot": False,
                                          "liquidity_usd": 50000.0})], "eth", "0x1")
    cov = coverage(assess_token(g["features"])["checks"])
    assert 0 < cov["readable"] < cov["total"]
    assert cov["basis"] != "full"


# ── the free source ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dexscreener_reads_the_deepest_pool():
    """An exit routes through the deepest pool, so that is the one whose
    liquidity matters — not the first in the array."""
    async def _fake(url):
        return {"pairs": [
            {"liquidity": {"usd": 1000}, "volume": {"h24": 10}},
            {"liquidity": {"usd": 90000}, "volume": {"h24": 5000},
             "pairCreatedAt": 1700000000000},
        ]}

    g = await gather([DexScreenerSource(transport=_fake)], "eth", "0x1")
    assert g["features"]["liquidity_usd"] == 90000.0
    assert g["features"]["volume_24h_usd"] == 5000.0


@pytest.mark.asyncio
async def test_a_token_dexscreener_does_not_know_is_empty_not_safe():
    """The whole point of the class docstring. No indexed pair is a fact about
    liquidity, not a clean bill of health."""
    async def _fake(url):
        return {"pairs": []}

    g = await gather([DexScreenerSource(transport=_fake)], "eth", "0x1")
    assert g["results"][0]["status"] == EMPTY
    assert g["features"] == {}


@pytest.mark.asyncio
async def test_dexscreener_never_claims_a_honeypot_verdict():
    """It has no such data. A source inventing a field it cannot see is the
    worst failure available to this layer."""
    async def _fake(url):
        return {"pairs": [{"liquidity": {"usd": 1.0}}]}

    g = await gather([DexScreenerSource(transport=_fake)], "eth", "0x1")
    for invented in ("honeypot", "can_sell", "verdict", "safe", "mintable"):
        assert invented not in g["features"]


@pytest.mark.asyncio
async def test_unreadable_numbers_do_not_become_zero():
    async def _fake(url):
        return {"pairs": [{"liquidity": {"usd": "n/a"}, "volume": {"h24": None}}]}

    g = await gather([DexScreenerSource(transport=_fake)], "eth", "0x1")
    assert "liquidity_usd" not in g["features"]
    assert "volume_24h_usd" not in g["features"]


@pytest.mark.asyncio
async def test_a_nan_is_not_a_reading():
    """Found by a surviving mutant. JSON `NaN` parses to a float, so every
    `float(x)` guard lets it through — and NaN as a liquidity figure is worse
    than a missing one: it compares False against every threshold, so a floor
    check passes it silently."""
    async def _fake(url):
        return {"pairs": [{"liquidity": {"usd": float("nan")},
                           "volume": {"h24": float("nan")}}]}

    g = await gather([DexScreenerSource(transport=_fake)], "eth", "0x1")
    assert "liquidity_usd" not in g["features"]
    assert g["results"][0]["status"] == EMPTY


@pytest.mark.asyncio
async def test_a_broken_payload_is_an_error_not_a_reading():
    async def _fake(url):
        raise RuntimeError("HTTP 502")

    g = await gather([DexScreenerSource(transport=_fake)], "eth", "0x1")
    assert g["results"][0]["status"] == ERROR


# ── junk ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_sources_is_not_a_crash():
    g = await gather([], "eth", "0x1")
    assert g["asked"] == 0 and g["features"] == {}
    assert human_readable(g)
    assert human_readable(None)
