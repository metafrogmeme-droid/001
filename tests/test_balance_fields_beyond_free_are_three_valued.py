"""`free` was not the only field minted from an absent balance-coin entry.

RC-2026-017, the rest of it. The finding named one line and it was fixed. This
file covers the three sites that made the SAME claim and were not:

- `fetch_balance`'s `used`, on the line DIRECTLY BELOW the one that was fixed.
  It was listed as "deliberately untouched" alongside `total`, but that list's
  stated reason -- `bot/main.py` classifies its startup auth halt on `total`
  and `error` -- covers `total`. `used` had been swept in by proximity.
- the `/venue` switch preflight, `float(acct.get("free") or 0)`, printed under
  a green "Venue switched" banner.
- `exchange_credentials._balance_total`, which returned 0.0 on any malformed
  shape and whose caller published that as
  `ok: True, equity_usd: 0.0, detail: "0.00 USDC total"` -- an affirmative
  success on the flow where someone has just linked an account.

AND THE FINDING'S REMAINING OPEN CLAIM, WHICH IS REFUTED HERE. The register
said `free` "can still arrive unreadable for a USDC-margined venue". Executed
rather than read: it cannot, on any of the three shapes ccxt produces for one.
`venues.py` already sets `balance_coin = "USDC"` for Hyperliquid and Paradex,
and ccxt's own `safe_balance` derives `free = total - used` whenever a venue
reports the other two -- which the Hyperliquid futures path does. The tests
below drive ccxt's real parser, so if a future ccxt stops deriving it, this
notices rather than the claim silently becoming true again.
"""
from __future__ import annotations

import asyncio

import ccxt
import pytest

from bot.core.exchange_credentials import _balance_total
from bot.core.live_executor import LiveExecutor
from bot.core.margin_clamp import read_money_field
from bot.core.venues import get_venue

# ── the refuted claim: a USDC venue DOES report a readable free margin ─────

def _hyperliquid_perp_balance(margin_mode: str) -> dict:
    """ccxt's OWN parse of a realistic Hyperliquid perp response."""
    ex = ccxt.hyperliquid()
    raw = {
        "marginSummary": {"accountValue": "1481.844", "totalMarginUsed": "300.0"},
        "withdrawable": "1181.844",
        "time": 1735689600000,
    }
    data = ex.safe_dict(raw, "marginSummary", {})
    usdc = {"total": ex.safe_number(data, "accountValue")}
    if margin_mode == "isolated":
        usdc["free"] = ex.safe_number(raw, "withdrawable")
    else:
        usdc["used"] = ex.safe_number(data, "totalMarginUsed")
    return ex.safe_balance({"info": raw, "USDC": usdc})


@pytest.mark.parametrize("margin_mode", ["cross", "isolated"])
def test_a_usdc_venue_reports_a_readable_free_margin(margin_mode):
    """The register's remaining open claim, refuted by execution.

    Cross margin is the one that looks alarming: Hyperliquid's own branch sets
    `total` and `used` and never sets `free`. ccxt's `safe_balance` then
    derives it. If that ever stops, every live order on the venue would be
    refused as "unreadable" -- correctly, but the refusal would be new.
    """
    entry = _hyperliquid_perp_balance(margin_mode)["USDC"]
    assert read_money_field(entry, "free") == pytest.approx(1181.844)


def test_the_usdc_venues_already_ask_for_the_right_coin():
    """`balance.get(balance_coin)` is why the claim was plausible."""
    for vid in ("hyperliquid", "paradex"):
        assert get_venue(vid).balance_coin == "USDC"
    assert get_venue("bitget").balance_coin == "USDT"


# ── `used`, through the real fetch_balance ────────────────────────────────

class _StubExchange:
    def __init__(self, payload):
        self.payload = payload

    async def fetch_balance(self):
        return self.payload


def _fetch_balance(venue: str, payload: dict) -> dict:
    """Drive the REAL LiveExecutor.fetch_balance over a planted payload."""
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._venue = get_venue(venue)
    ex._exchange = _StubExchange(payload)

    async def _get_exchange():
        return ex._exchange

    ex._get_exchange = _get_exchange
    return asyncio.run(LiveExecutor.fetch_balance(ex))


def test_an_absent_balance_coin_entry_does_not_mint_a_used_of_zero():
    out = _fetch_balance("hyperliquid", {"info": {}, "USDT": {"free": 5.0}})
    assert out["used"] is None, (
        "an absent USDC entry produced a `used` of 0.0, which the "
        "/livebalance card renders as 'Used $0.00' -- nothing is deployed, "
        "said about an account nobody could read"
    )
    assert out["free"] is None


def test_a_real_zero_used_is_still_a_reading():
    """`0.0` means no margin in use. It must not read as 'not reported'."""
    out = _fetch_balance("bitget",
                         {"info": {}, "USDT": {"free": 10.0, "used": 0.0,
                                               "total": 10.0}})
    assert out["used"] == 0.0
    assert out["used"] is not None


def test_a_normal_used_reads_through():
    out = _fetch_balance("bitget",
                         {"info": {}, "USDT": {"free": 900.0, "used": 100.0,
                                               "total": 1000.0}})
    assert out["used"] == 100.0
    assert out["free"] == 900.0


def test_total_stays_a_number_on_purpose():
    """The one field that must NOT become None, and why.

    `bot/main.py` decides the venue authenticated with
    `float(bal.get("total", 0) or 0) > 0`. A None there would report a
    healthy, funded account as an empty one on every startup. If you are here
    because you wanted to make `total` three-valued too, fix that call site
    first.
    """
    out = _fetch_balance("hyperliquid", {"info": {}, "USDT": {"free": 5.0}})
    assert out["total"] == 0.0
    assert isinstance(out["total"], float)


# ── the /venue switch banner ──────────────────────────────────────────────

def test_the_venue_switch_banner_does_not_print_an_unread_balance():
    """The venue answered, but with no entry for the margin coin."""
    from bot.skills.engine_ops_commands import venue_balance_line
    line = venue_balance_line({}, "USDC")
    assert "could not be read" in line
    assert "0.00" not in line, (
        "the line under a green 'Venue switched' banner printed a zero for a "
        "balance nobody read"
    )


def test_the_venue_switch_banner_states_each_half_separately():
    from bot.skills.engine_ops_commands import venue_balance_line
    line = venue_balance_line({"total": 1481.844}, "USDC")
    assert "1,481.84 USDC" in line
    assert "free unknown" in line


def test_a_real_venue_switch_balance_still_renders():
    from bot.skills.engine_ops_commands import venue_balance_line
    line = venue_balance_line({"total": 1481.844, "free": 1181.844}, "USDC")
    assert "1,481.84 USDC" in line and "1,181.84" in line
    assert "unknown" not in line and "could not be read" not in line


def test_a_genuinely_empty_account_is_not_reported_as_unreadable():
    """The other direction: 0.0 is a reading and must still print as one."""
    from bot.skills.engine_ops_commands import venue_balance_line
    line = venue_balance_line({"total": 0.0, "free": 0.0}, "USDC")
    assert "0.00 USDC" in line
    assert "could not be read" not in line and "unknown" not in line


# ── _balance_total, on the credential-connect flow ────────────────────────

@pytest.mark.parametrize("bal", [
    {"USDT": {"total": 5.0}},          # currency entry absent
    {"USDC": "nonsense"},              # malformed row
    {},                                # nothing at all
    None,                              # not a dict
])
def test_an_unreadable_balance_total_is_none_not_zero(bal):
    assert _balance_total(bal, "USDC") is None, (
        "balance_snapshot publishes this as ok:True, equity_usd:0.00 -- an "
        "affirmative success telling a user their just-linked account is empty"
    )


def test_a_genuinely_empty_balance_total_is_zero():
    assert _balance_total({"USDC": {"total": 0.0}}, "USDC") == 0.0


def test_a_balance_total_falls_back_to_free_plus_used():
    assert _balance_total(
        {"USDC": {"free": 1181.8, "used": 300.0}}, "USDC") == pytest.approx(1481.8)


def test_the_networth_card_renders_an_unreadable_equity_as_unavailable():
    """The consumer, checked rather than assumed.

    RC-2026-015's second defect was an honest fix upstream becoming a
    TypeError at a consumer nobody checked. This is that check.
    """
    from bot.skills.telegram_handler import TelegramHandler
    out = TelegramHandler._format_networth(
        None, {"connected": True, "venue": "hyperliquid", "ok": True,
               "equity_usd": None, "detail": "no readable USDC balance"})
    assert "unavailable" in out
    assert "$0.00" not in out


# ── wiring: the seam exists AND the call site feeds it a reading ──────────

def _code_only(src: str) -> str:
    """Source with comments and docstrings removed.

    CLAUDE.md records four false failures from a comment quoting the string a
    test forbids, and this file would have been the fifth: the comment added
    above the fixed read spells out `float(acct.get("free") or 0)` to say what
    it replaced.
    """
    import io
    import tokenize
    out = []
    prev_type = None
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev_type in (
                tokenize.INDENT, tokenize.NEWLINE, tokenize.NL, None):
            continue          # a docstring, not a value
        out.append(tok.string)
        if tok.type not in (tokenize.NL, tokenize.NEWLINE):
            prev_type = tok.type
    return " ".join(out)


def _fabricated_zero_shapes(fn) -> list[str]:
    """Every `X or 0` and `X.get(k, 0)` inside `fn`, by SHAPE not by name.

    The string version of this check keyed on the variable name `acct` and a
    mutation that re-minted the zeros as `_a.get("total") or 0` sailed through
    all 21 tests. That is the trap CLAUDE.md already records once -- a colour
    assertion that "passed against a mutation reintroducing it under a
    different variable name two lines up" -- collected again, in the test
    written to prevent it. An AST walk cannot be renamed around.
    """
    import inspect
    import textwrap

    return _fabricated_zero_shapes_in(textwrap.dedent(inspect.getsource(fn)))


def _fabricated_zero_shapes_in(src: str) -> list[str]:
    import ast

    tree = ast.parse(src)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            for v in node.values[1:]:
                if isinstance(v, ast.Constant) and v.value in (0, 0.0):
                    hits.append(f"`... or {v.value}` at line {node.lineno}")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and len(node.args) == 2):
            d = node.args[1]
            if isinstance(d, ast.Constant) and d.value in (0, 0.0):
                hits.append(f"`.get(..., {d.value})` at line {node.lineno}")
    return hits


def test_the_venue_switch_call_site_mints_no_zero_of_its_own():
    """A pure renderer nothing feeds honestly is the #999 shape.

    `venue_balance_line` can be perfect and still print zeros forever if the
    caller hands it numbers it invented. Only the call site can answer that.
    """
    from bot.skills.telegram_handler import TelegramHandler
    hits = _fabricated_zero_shapes(TelegramHandler._cmd_venue)
    assert hits == [], (
        "_cmd_venue defaults a balance field to zero: " + "; ".join(hits) +
        ". The raw entry goes to venue_balance_line, which is the only place "
        "that decides what was read."
    )


def test_the_venue_switch_call_site_still_goes_through_the_renderer():
    import inspect
    import textwrap

    from bot.skills.telegram_handler import TelegramHandler
    src = _code_only(textwrap.dedent(
        inspect.getsource(TelegramHandler._cmd_venue)))
    assert "venue_balance_line ( acct , coin )" in src, (
        "the /venue banner no longer goes through the renderer"
    )


def test_the_shape_check_catches_a_renamed_reintroduction():
    """Guard the guard, against the exact mutation that survived the string version."""
    src = ("def f(bal):\n"
           "    _a = bal if isinstance(bal, dict) else {}\n"
           "    return {'total': float(_a.get('total') or 0),\n"
           "            'free': float(_a.get('free', 0))}\n")
    hits = _fabricated_zero_shapes_in(src)
    assert len(hits) >= 2, (
        f"the shape check missed a renamed reintroduction: {hits}"
    )


def test_the_comment_stripper_would_have_caught_a_real_regression():
    """Guard the guard: prove _code_only removes comments and not code."""
    src = 'x = 1  # acct.get("free") or 0\ny = acct.get("free") or 0\n'
    stripped = _code_only(src)
    assert stripped.count('acct . get ( "free" ) or') == 1, (
        "the stripper removed real code, or failed to remove a comment"
    )


def test_a_non_dict_venue_entry_is_unreadable_not_empty():
    """The widened seam covers this; the old signature could not see it.

    `bal.get(coin, {})` yields whatever the venue put there. A string, a list
    or None is not a balance, and reading it as one is how `or 0` produced a
    zero in the first place.
    """
    from bot.skills.engine_ops_commands import venue_balance_line
    for entry in ("nonsense", None, [], 0):
        line = venue_balance_line(entry, "USDC")
        assert "could not be read" in line, f"{entry!r} rendered as a balance"
        assert "0.00" not in line
