"""The generic leverage guard, DRIVEN — it decides whether a live order runs.

``LiveExecutor._ensure_leverage_generic`` is the non-Bitget half of the control
that keeps a real order from being placed at the wrong leverage. It reads the
venue back after setting, retries once on a mismatch, and **aborts the order**
when the venue stays stuck — because trading at a venue's sticky default is
wrong risk sizing on real money, and running CROSS while config says isolated
exposes the whole account to one liquidation.

Every assertion about it was ``inspect.getsource``::

    src = inspect.getsource(LiveExecutor._ensure_leverage_generic)
    assert "fetch_leverage" in src
    assert "ABORTING" in src
    assert "RuntimeError" in src

Seven such assertions across two files, and not one of them runs the function.
They pass against a body that mentions those names and does nothing with them;
they pass against a `raise RuntimeError` that is unreachable; they passed the
whole time nobody could say whether the retry read the venue a second time or
the first value twice. CLAUDE.md names this exact shape — "a source scan
standing in for behaviour nothing else tests" — and ranks candidates by what a
wrong claim would cost. A wrong claim here is a live position at 10x that the
sizing math priced at 3x.

MEASURED, NOT ASSERTED. Replacing the ``raise RuntimeError(...)`` below with a
``logger.error`` of the same message — i.e. letting a live order proceed at the
venue's stuck leverage after announcing it would not — leaves all four source
assertions PASSING (the word "ABORTING" survives in the log line above it, and
"RuntimeError" survives in the ``except RuntimeError: raise`` two lines down)
and fails exactly two tests in this file.

**The seam already existed**: the method takes `exchange` as a parameter, so
there was nothing to extract — only a fake to write. That is the whole cost of
the conversion, and it immediately pins behaviour the scans could not reach:
that the abort is raised rather than logged, that the retry re-reads, that an
UNVERIFIABLE venue warns and lets the order through (the opposite decision from
a stuck one, and the two are one `if` apart), and that the warning fires once
per symbol rather than on every order.

The source scans in ``test_venue_safety_hardening.py`` and
``test_venue_abstraction.py`` are deliberately left alone: per CLAUDE.md, "do
not convert wholesale" — they lock WIRING (that the venue hooks are consulted
at all), which is a different claim from the behaviour pinned here.
"""

import logging

import pytest

from bot.core.live_executor import LiveExecutor

TARGET = 5


class FakeExchange:
    """The venue, answering exactly as scripted.

    ``leverage_reads`` is consumed one entry per ``fetch_leverage`` call, so a
    test can say "mismatched, then correct" and the retry path has to actually
    read twice to see the second answer. An entry that is an exception class is
    raised; anything else is returned.
    """

    def __init__(self, leverage_reads=None, market=None, positions=None,
                 set_leverage_raises=None):
        self._leverage_reads = list(leverage_reads or [])
        self._market = market if market is not None else {}
        self._positions = positions if positions is not None else []
        self._set_leverage_raises = set_leverage_raises
        self.set_leverage_calls: list = []
        self.set_margin_mode_calls: list = []
        self.fetch_leverage_calls: list = []

    def market(self, sym):
        return self._market

    async def set_margin_mode(self, mode, sym):
        self.set_margin_mode_calls.append((mode, sym))

    async def set_leverage(self, leverage, sym, params=None):
        self.set_leverage_calls.append(leverage)
        if self._set_leverage_raises is not None:
            raise self._set_leverage_raises

    async def fetch_leverage(self, sym, params=None):
        self.fetch_leverage_calls.append(sym)
        if not self._leverage_reads:
            return {}
        nxt = self._leverage_reads.pop(0)
        if isinstance(nxt, type) and issubclass(nxt, BaseException):
            raise nxt("venue read-back unavailable")
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    async def fetch_positions(self, syms, params=None):
        return self._positions


@pytest.fixture
def ex(tmp_path, monkeypatch):
    """An executor whose target leverage is fixed, so the tests are about the
    verification and not about the sizing model that chooses the number."""
    e = LiveExecutor(state_dir=str(tmp_path))
    monkeypatch.setattr(e, "_compute_target_leverage", lambda symbol: TARGET)
    return e


@pytest.fixture
def audits(monkeypatch):
    """Capture audit() rather than letting it reach the real trade log."""
    seen: list[dict] = []

    def _fake_audit(log, message, **kw):
        seen.append({"message": message, **kw})

    monkeypatch.setattr("bot.core.live_executor.audit", _fake_audit)
    return seen


# ── the abort: the venue will not take the leverage ──────────────────────


@pytest.mark.asyncio
async def test_stuck_leverage_aborts_the_order(ex, audits):
    """Two reads, both wrong → RuntimeError, so execute() never places."""
    fake = FakeExchange(leverage_reads=[{"leverage": 10}, {"leverage": 10}])
    with pytest.raises(RuntimeError) as err:
        await ex._ensure_leverage_generic(fake, "BTC/USDT")
    msg = str(err.value)
    assert "10" in msg and str(TARGET) in msg, (
        "the abort must name what was wanted and what the venue reports — an "
        f"operator reads this to decide what to do: {msg}")
    # It re-read after retrying. Asserting only "raised" would pass against a
    # body that compared the FIRST reading to itself.
    assert len(fake.fetch_leverage_calls) == 2
    assert fake.set_leverage_calls == [TARGET, TARGET], (
        "the retry must actually re-issue set_leverage before re-reading")


@pytest.mark.asyncio
async def test_a_retry_that_takes_does_not_abort(ex, audits):
    """Mismatched, then correct → the order proceeds. The near-identical
    scenario to the one above, and the source scans cannot tell them apart."""
    fake = FakeExchange(leverage_reads=[{"leverage": 10}, {"leverage": TARGET}])
    await ex._ensure_leverage_generic(fake, "BTC/USDT")   # must not raise
    assert len(fake.fetch_leverage_calls) == 2


@pytest.mark.asyncio
async def test_correct_on_the_first_read_does_not_retry(ex, audits):
    fake = FakeExchange(leverage_reads=[{"leverage": TARGET}])
    await ex._ensure_leverage_generic(fake, "BTC/USDT")
    assert fake.set_leverage_calls == [TARGET], "a matching venue was set twice"
    assert len(fake.fetch_leverage_calls) == 1


@pytest.mark.asyncio
async def test_longLeverage_is_read_when_leverage_is_absent(ex, audits):
    """ccxt venues disagree about the key. A stuck venue reporting under
    `longLeverage` must abort just the same — reading only `leverage` would
    make it UNVERIFIED, which is the branch that lets the order through."""
    fake = FakeExchange(leverage_reads=[{"longLeverage": 10}, {"longLeverage": 10}])
    with pytest.raises(RuntimeError):
        await ex._ensure_leverage_generic(fake, "BTC/USDT")


# ── unverifiable is NOT stuck, and must not be treated as it ─────────────


@pytest.mark.asyncio
async def test_unreadable_leverage_warns_and_lets_the_order_through(ex, audits):
    """A venue with no read-back is not a venue that refused the setting.

    Aborting here would take a venue offline for lacking an endpoint; treating
    it as verified would be the fail-open this repo keeps finding. It warns,
    loudly and once, and proceeds — and the audit says the leverage is
    UNCONFIRMED rather than reporting the target as though it had been read.
    """
    fake = FakeExchange(leverage_reads=[{}])   # no leverage key at all
    await ex._ensure_leverage_generic(fake, "BTC/USDT")   # must not raise
    unver = [a for a in audits if a.get("action") == "leverage_unverified"]
    assert len(unver) == 1, f"expected one unverified audit, got {audits}"
    assert unver[0]["result"] == "WARNING"
    assert unver[0]["level"] == logging.WARNING
    assert "without confirmation" in unver[0]["message"], (
        "the audit must say the reading is unconfirmed, not just report the "
        f"target: {unver[0]['message']}")


@pytest.mark.asyncio
async def test_a_raising_read_back_does_not_abort(ex, audits):
    """A venue whose read-back throws is unverifiable, not stuck."""
    fake = FakeExchange(leverage_reads=[ValueError("read-back unavailable")])
    await ex._ensure_leverage_generic(fake, "BTC/USDT")   # must not raise


@pytest.mark.asyncio
async def test_a_venue_RuntimeError_propagates_as_an_abort(ex, audits):
    """A SHARP EDGE, PINNED RATHER THAN CHANGED.

    The abort is raised as a bare ``RuntimeError`` and propagated by
    ``except RuntimeError: raise``, so the handler cannot tell its own abort
    from a ``RuntimeError`` raised by the venue client — and a read-back that
    fails that way aborts the order with "stuck at Nx", a verdict nobody
    measured.

    It errs SAFE: the outcome is no trade, which is the correct direction to
    fail on a control that decides position sizing, and ccxt raises from its
    own ``BaseError`` hierarchy so the path is narrow in practice. Recorded
    here rather than fixed because the fix (a ``RuntimeError`` subclass for the
    abort) edits the live order path to buy a message, and this is not the
    change to make on the way to a deploy. The test is what makes it a known
    edge instead of a surprise.
    """
    fake = FakeExchange(leverage_reads=[RuntimeError("venue client blew up")])
    with pytest.raises(RuntimeError):
        await ex._ensure_leverage_generic(fake, "BTC/USDT")


@pytest.mark.asyncio
async def test_the_unverified_warning_is_once_per_symbol(ex, audits):
    """Per-symbol, not per-order: an unverifiable venue would otherwise write
    an audit line on every single order and bury the ones that matter."""
    for _ in range(3):
        await ex._ensure_leverage_generic(FakeExchange(leverage_reads=[{}]), "BTC/USDT")
    assert len([a for a in audits if a.get("action") == "leverage_unverified"]) == 1
    await ex._ensure_leverage_generic(FakeExchange(leverage_reads=[{}]), "ETH/USDT")
    assert len([a for a in audits if a.get("action") == "leverage_unverified"]) == 2, (
        "a different symbol is a different unverified position")


# ── the market cap, which silently changes what 'correct' means ──────────


@pytest.mark.asyncio
async def test_target_is_clamped_to_the_market_max(ex, audits):
    """Hyperliquid caps leverage per symbol. The clamp must apply BEFORE the
    verification, or a capped venue reports its own max, mismatches the
    unclamped target, and aborts every order on that symbol."""
    fake = FakeExchange(market={"limits": {"leverage": {"max": 3}}},
                        leverage_reads=[{"leverage": 3}])
    await ex._ensure_leverage_generic(fake, "BTC/USDT")   # must not raise
    assert fake.set_leverage_calls == [3], (
        f"the market cap was not applied to the set call: {fake.set_leverage_calls}")


@pytest.mark.asyncio
async def test_an_unloaded_market_does_not_block_the_set(ex, audits):
    """`exchange.market()` raising is an ordinary cold-start state."""
    class NoMarket(FakeExchange):
        def market(self, sym):
            raise RuntimeError("markets not loaded")

    fake = NoMarket(leverage_reads=[{"leverage": TARGET}])
    await ex._ensure_leverage_generic(fake, "BTC/USDT")
    assert fake.set_leverage_calls == [TARGET]


# ── margin mode: loud, and deliberately not an abort ─────────────────────


# `CONFIG.exchange` is a FROZEN dataclass, so these read the configured mode
# and derive the venue's answer from it rather than hardcoding a pair. That is
# the better test anyway: the property is "venue disagrees with config", and a
# test asserting the literal strings would pass while agreeing for the wrong
# reason if the default ever changed.
def _configured_mode() -> str:
    from bot.config import CONFIG
    return "cross" if CONFIG.exchange.margin_mode in ("cross", "crossed") else "isolated"


def _opposite_mode() -> str:
    return "isolated" if _configured_mode() == "cross" else "cross"


@pytest.mark.asyncio
async def test_a_disagreeing_margin_mode_is_audited(ex, audits):
    """CROSS while config says isolated is whole-account liquidation exposure.

    It is audited CRITICAL and does NOT abort — the position already exists by
    the time the venue reports its mode, so refusing here would leave it open
    and unmanaged. Pinning the non-abort matters as much as pinning the audit:
    it is a decision, and without a test it reads as a missing `raise`.
    """
    fake = FakeExchange(leverage_reads=[{"leverage": TARGET}],
                        positions=[{"marginMode": _opposite_mode()}])
    await ex._ensure_leverage_generic(fake, "BTC/USDT")   # must not raise
    mism = [a for a in audits if a.get("action") == "margin_mode_mismatch"]
    assert len(mism) == 1, f"a disagreeing margin mode was not audited: {audits}"
    assert mism[0]["result"] == "CRITICAL"
    assert mism[0]["data"]["actual"] == _opposite_mode()
    assert mism[0]["data"]["expected"] == _configured_mode()


@pytest.mark.asyncio
async def test_a_matching_margin_mode_is_not_audited(ex, audits):
    fake = FakeExchange(leverage_reads=[{"leverage": TARGET}],
                        positions=[{"marginMode": _configured_mode()}])
    await ex._ensure_leverage_generic(fake, "BTC/USDT")
    assert not [a for a in audits if a.get("action") == "margin_mode_mismatch"]


@pytest.mark.asyncio
async def test_an_unreadable_margin_mode_is_not_reported_as_matching(ex, audits):
    """An empty marginMode is not evidence of agreement. It must not be audited
    as a mismatch either — nobody read it, so there is nothing to report."""
    fake = FakeExchange(leverage_reads=[{"leverage": TARGET}],
                        positions=[{"marginMode": ""}])
    await ex._ensure_leverage_generic(fake, "BTC/USDT")
    assert not [a for a in audits if a.get("action") == "margin_mode_mismatch"]


# ── the "already set" family, which must not read as a failure ───────────


@pytest.mark.asyncio
async def test_already_set_is_not_an_error(ex, audits, caplog):
    """Venues answer a redundant set_leverage with "already set"/"not modified".
    Treating those as failures would fill the log with alarms on every order."""
    fake = FakeExchange(leverage_reads=[{"leverage": TARGET}],
                        set_leverage_raises=RuntimeError("leverage not modified"))
    with caplog.at_level(logging.WARNING, logger="bot.core.live_executor"):
        await ex._ensure_leverage_generic(fake, "BTC/USDT")
    assert not [r for r in caplog.records if "Leverage/margin set failed" in r.message]
