"""/etf draws the website's ETF flows reading, and computes nothing of its own.

`app/lib/etf_flows.js` is the one reading of US spot crypto ETF flows: the
week's sums per asset, the coin estimates, the provider grouping and the
takeaway. The Markets panel renders it, and the card route carries the payload
as ``data`` beside the text card so Telegram's ``/etf`` can draw the same
figures as a picture. A second copy of that arithmetic in Python would be a
second answer about one reading, so `bot/formatters/etf_card.py` only lays the
payload out.

What the picture must not do, driven on planted payloads:

  - print a figure the payload does not hold as ``$0``: an absence is an em
    dash in the muted colour;
  - colour a measured zero or an absence green or red: colour follows the
    sign of a figure that was read;
  - draw an asset the source did not answer as a bar: it gets a sentence;
  - draw a picture of nothing: a payload with no asset read answers ``b""``
    and the command sends the text card instead.

And the command: the fetch is the ``etf_flows`` card, a channel that did not
answer gets the transport's sentence, a picture that could not be drawn or
sent falls back to the text, and the gate runs under the command's own name.
"""
from __future__ import annotations

import copy
import inspect
import json
import pathlib
import shutil
import subprocess
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

import bot.formatters.etf_card as ec
import bot.skills.market_commands as mc
import bot.utils.web_data_pull as wdp
from bot.formatters.etf_card import coins_signed, render_etf_card, usd_signed
from bot.skills.telegram_handler import TelegramHandler
from bot.utils.user_store import ROLE_PERMISSIONS
from bot.utils.web_data_pull import WEB_CARDS
from tests.png_text import drawn, fill_of, strings

# The shape app/lib/etf_flows.js builds (`buildEtfFlows`), taken from a drive
# of the Node reading over planted source rows.
PAYLOAD = {
    "source": {"name": "SoSoValue", "url": "https://sosovalue.com/assets/etf/us-btc-spot"},
    "window_days": 7,
    "as_of": "2026-09-28",
    "window_start": "2026-09-22",
    "assets": [
        {"key": "btc", "symbol": "BTC", "name": "Bitcoin", "read": True, "latest_date": "2026-09-28",
         "week": {"net_flow_usd": 500000000, "days_read": 5, "days_listed": 5, "coins": 5000,
                  "approx": True},
         "latest_day_flow_usd": 100000000, "funds_date": "2026-09-28",
         "providers": [
             {"provider": "BlackRock", "tickers": ["IBIT"], "net_flow_usd": 80000000,
              "funds": 1, "funds_read": 1},
             {"provider": "Fidelity", "tickers": ["FBTC"], "net_flow_usd": 20000000,
              "funds": 1, "funds_read": 1},
             {"provider": "Invesco", "tickers": ["BTCO"], "net_flow_usd": 0,
              "funds": 1, "funds_read": 1},
         ]},
        {"key": "eth", "symbol": "ETH", "name": "Ether", "read": True, "latest_date": "2026-09-28",
         "week": {"net_flow_usd": 0, "days_read": 5, "days_listed": 5, "coins": 0, "approx": True},
         "latest_day_flow_usd": 0, "funds_date": None, "providers": []},
        {"key": "sol", "symbol": "SOL", "name": "Solana", "read": False, "reason": "unavailable"},
    ],
    "total": {"net_flow_usd": 500000000, "assets_in_total": ["BTC", "ETH"], "assets_read": 2,
              "assets_tracked": 3},
    "takeaway": {"symbol": "BTC", "coins": 5000, "issuance_per_day": 450,
                 "days_of_issuance": 11.11111111111111, "direction": "inflow"},
}


def _payload(**changes):
    p = copy.deepcopy(PAYLOAD)
    for path, value in changes.items():
        node = p
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[int(k)] if isinstance(node, list) else node[k]
        last = keys[-1]
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
    return p


def _rows(payload=None):
    return drawn(render_etf_card, payload if payload is not None else _payload())


# ── 1. the formatters: an absence is a dash, a bool is not a figure ──────────

class TestTheFormatters:
    @pytest.mark.parametrize("v,want", [
        (500000000, "+$500.0M"), (-1300000000, "-$1.30B"), (12500, "+$12.5K"),
        (0, "$0"), (0.0, "$0"), (-7, "-$7"),
        (None, "—"), (True, "—"), (False, "—"), ("n/a", "—"),
        (float("nan"), "—"), (float("inf"), "—"),
    ])
    def test_usd_signed(self, v, want):
        assert usd_signed(v) == want

    @pytest.mark.parametrize("v,want", [
        (16971.4, "≈ +16,971 BTC"), (-3.25, "≈ -3.3 BTC"), (0.0125, "≈ +0.013 BTC"),
        (0, "≈ 0 BTC"), (3.0, "≈ +3 BTC"), (2.5, "≈ +2.5 BTC"),
    ])
    def test_coins_are_an_estimate_and_say_so(self, v, want):
        assert coins_signed(v, "BTC") == want

    @pytest.mark.parametrize("v", [None, True, "x", float("nan")])
    def test_an_absent_coin_estimate_is_no_line(self, v):
        assert coins_signed(v, "BTC") is None


# The figures the website prints and the picture prints are ONE reading, so
# the two formatters are driven over one table and must agree character for
# character. Exact ties are in it on purpose: Python's format rounds a tie to
# even and the website's `toFixed` rounds it up, and before the picture
# rounded the website's way, -3.25 coins read "-3.2" here beside "-3.3" on
# the panel.
ETF_JS = pathlib.Path(__file__).resolve().parent.parent / "app" / "lib" / "etf_flows.js"
AGREEMENT = [0, 0.0, -0.0, 1, -7, 2.5, 3.25, -3.25, 0.0125, 0.0005, 99.95, 100, 999.5,
             1000, 1234.55, 12500, 999999, 2250000, -1300000000, 1.005e9, 5e11,
             None, True, False, "12", "", "n/a", float("inf")]


def _js_formats(values):
    if not shutil.which("node"):
        pytest.skip("node not available")
    js = (
        f"const e = require({json.dumps(str(ETF_JS))});"
        "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
        "const vs=JSON.parse(s).map(v=>v==='__inf__'?Infinity:v);"
        "process.stdout.write(JSON.stringify(vs.map(v=>[e.usdSigned(v),e.coinsSigned(v,'BTC')])));});"
    )
    payload = json.dumps(["__inf__" if v == float("inf") else v for v in values])
    res = subprocess.run(["node", "-e", js], input=payload, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr[:400]
    return json.loads(res.stdout)


def test_the_picture_prints_each_figure_as_the_website_does():
    js = _js_formats(AGREEMENT)
    py = [[usd_signed(v), coins_signed(v, "BTC")] for v in AGREEMENT]
    diff = [(v, j, p) for v, j, p in zip(AGREEMENT, js, py) if j != p]
    assert not diff, diff


# Every tie-shaped figure, not a table of the ones that happened to agree.
# The coin estimate is `toLocaleString` on the website, which rounds the
# shortest decimal spelling: 1.45 read "+1.5" there and "+1.4" here. Rounding
# the binary value, 1,784 of the 7,802 figures below (3,901 magnitudes, each
# signed both ways) printed differently, while the hand-picked table above
# passed.
TIES = sorted({
    *(round(k * 0.05, 2) for k in range(0, 2001)),            # one-decimal ties under 100
    *(round(i * 0.001 + 0.0005, 4) for i in range(0, 1000)),  # three-decimal ties under 1
    *(k + 0.5 for k in range(100, 1000)),                     # whole-coin ties
})


def test_every_tie_shaped_figure_prints_as_the_website_prints_it():
    values = [*TIES, *(-v for v in TIES)]
    js = _js_formats(values)
    py = [[usd_signed(v), coins_signed(v, "BTC")] for v in values]
    diff = [(v, j, p) for v, j, p in zip(values, js, py) if j != p]
    assert not diff, (len(diff), diff[:8])


@pytest.mark.parametrize("v, want", [
    (1.45, "≈ +1.5 BTC"), (3.55, "≈ +3.6 BTC"), (-1.95, "≈ -2 BTC"),
    (0.1235, "≈ +0.124 BTC"), (99.95, "≈ +100 BTC"),
])
def test_a_coin_tie_rounds_its_written_value(v, want):
    """Without node: the readings the website was measured to print."""
    assert coins_signed(v, "BTC") == want


# ── 2. nothing to draw is not a picture ──────────────────────────────────────

class TestNothingToDraw:
    @pytest.mark.parametrize("payload", [None, [], "card", 3, {}, {"assets": []},
                                         {"assets": [{"key": "btc", "read": False}]},
                                         {"assets": [{"key": "btc", "read": "yes"}]}])
    def test_no_asset_read_is_no_picture(self, payload):
        assert render_etf_card(payload) == b""

    def test_a_payload_with_one_asset_read_is_a_png(self):
        png = render_etf_card(_payload())
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ── 3. the picture: the payload's figures, coloured by their sign ────────────

class TestThePicture:
    def test_the_net_flow_is_the_payloads_total_in_green(self):
        rows = _rows()
        assert fill_of(rows, "+$500.0M") == ec._GREEN
        assert "across BTC, ETH" in strings(render_etf_card, _payload())

    def test_net_redemptions_are_red(self):
        rows = _rows(_payload(total__net_flow_usd=-1300000000))
        assert fill_of(rows, "-$1.30B") == ec._RED

    def test_an_unread_total_is_a_muted_dash_not_zero(self):
        rows = _rows(_payload(total__net_flow_usd=None))
        assert fill_of(rows, "—") == ec._MUTED
        # The total's own cell: the NET FLOW box draws one big figure, and no
        # "$0" stands in for the absence.
        assert "$0" not in [t for t, _ in rows[:6]]

    def test_a_measured_zero_week_is_muted(self):
        rows = _rows()
        eth_values = [f for t, f in rows if t == "$0"]
        assert eth_values and all(f == ec._MUTED for f in eth_values)

    def test_an_asset_the_source_did_not_answer_gets_a_sentence_not_a_bar(self):
        rows = _rows()
        assert fill_of(rows, "not read (the source did not answer)") == ec._MUTED
        texts = [t for t, _ in rows]
        i = texts.index("SOL")
        assert texts[i + 1] == "not read (the source did not answer)"
        # Nothing about a flow follows it: no value beside a bar, no detail
        # line, which would print the absence as a dash where a figure goes.
        assert not any(t.startswith("last day") or " · last day " in t for t in texts[i + 2:])
        assert "—" not in texts[i + 2:i + 4]

    def test_an_unknown_reason_says_unreadable_and_never_echoes_it(self):
        p = _payload(assets__2__reason="<b>boom</b>")
        texts = strings(render_etf_card, p)
        assert "not read (unreadable)" in texts
        assert not any("boom" in t for t in texts)

    def test_the_asset_detail_line_carries_the_estimate_and_the_last_day(self):
        texts = strings(render_etf_card, _payload())
        assert "≈ +5,000 BTC · last day +$100.0M" in texts

    def test_a_partial_week_says_how_many_days_were_read(self):
        p = _payload(assets__0__week={"net_flow_usd": 400000000, "days_read": 4,
                                      "days_listed": 5, "coins": 4000})
        texts = strings(render_etf_card, p)
        assert "≈ +4,000 BTC · 4 of 5 days read · last day +$100.0M" in texts

    def test_an_asset_reported_on_an_earlier_day_says_it_is_not_in_the_total(self):
        p = _payload(assets__1__latest_date="2026-09-25")
        texts = strings(render_etf_card, p)
        assert any("last reported Sep 25, not in the total" in t for t in texts)

    def test_the_providers_are_the_payloads_own_with_their_sign(self):
        rows = _rows()
        texts = [t for t, _ in rows]
        assert "BTC FUNDS · SEP 28" in texts
        assert texts.index("BlackRock") < texts.index("Fidelity")
        assert fill_of(rows, "+$80.0M") == ec._GREEN
        # A provider that reported no flow is counted, not listed as a mover.
        assert "Invesco" not in texts
        assert fill_of(rows, "1 of 3 providers reported no flow") == ec._MUTED

    def test_a_provider_not_read_for_the_day_is_said(self):
        p = _payload(assets__0__providers__1={"provider": "Fidelity", "tickers": ["FBTC"],
                                              "net_flow_usd": None, "funds": 1, "funds_read": 0})
        texts = strings(render_etf_card, p)
        assert "Fidelity" not in texts
        assert "1 of 3 providers reported no flow · 1 not read for this day" in texts

    def test_no_mover_is_said_rather_than_an_empty_list(self):
        providers = [{"provider": "Invesco", "tickers": ["BTCO"], "net_flow_usd": 0,
                      "funds": 1, "funds_read": 1}]
        texts = strings(render_etf_card, _payload(assets__0__providers=providers))
        assert "No provider reported a flow on this day." in texts

    def test_the_takeaway_is_labelled_derived_with_its_constant(self):
        texts = strings(render_etf_card, _payload())
        assert "Net buying of ≈ 5,000 BTC is about 11 days of newly mined bitcoin" in texts
        assert "derived: 450 BTC a day since the 2024 halving" in texts

    def test_an_outflow_takeaway_is_net_selling(self):
        p = _payload(takeaway={"symbol": "BTC", "coins": -900, "issuance_per_day": 450,
                               "days_of_issuance": 2.0, "direction": "outflow"})
        texts = strings(render_etf_card, p)
        assert "Net selling of ≈ 900 BTC is about 2.0 days of newly mined bitcoin" in texts

    @pytest.mark.parametrize("days,span", [(12.5, "13"), (2.25, "2.3"), (9.95, "9.9"), (10.0, "10")])
    def test_the_days_round_as_the_website_rounds_them(self, days, span):
        # An exact tie rounds up, as `toFixed` does; Python's format would
        # print 12 where the website prints 13. 9.95 is not a tie: its
        # binary value is just below it, so both runtimes print 9.9.
        p = _payload(takeaway={"symbol": "BTC", "coins": 450 * days, "issuance_per_day": 450,
                               "days_of_issuance": days, "direction": "inflow"})
        texts = strings(render_etf_card, p)
        assert any(t.endswith(f"is about {span} days of newly mined bitcoin") for t in texts), texts

    def test_no_takeaway_draws_no_takeaway(self):
        texts = strings(render_etf_card, _payload(takeaway=None))
        assert not any("newly mined" in t or t.startswith("derived:") for t in texts)

    def test_the_source_and_its_date_are_on_the_picture(self):
        texts = strings(render_etf_card, _payload())
        assert "Source: SoSoValue · data as of 2026-09-28" in texts
        assert any("estimates" in t and "Market data only" in t for t in texts)

    def test_the_window_is_the_payloads(self):
        texts = strings(render_etf_card, _payload())
        assert "Weekly net creations and redemptions · Sep 22 – Sep 28" in texts


# ── 4. the command ───────────────────────────────────────────────────────────

CARD_TEXT_HTML = ("📊 <b>US spot crypto ETF flows</b> — Sep 22 – Sep 28<br>"
                  "Net: <b>+$500.0M</b> across BTC, ETH<br><br>"
                  "• BTC: +$500.0M<br>• ETH: $0<br>• SOL: not read (the source did not answer)<br>"
                  "<i>Source: SoSoValue · market data only</i>")


def _host(photo_ok=True):
    return NS(_guard=AsyncMock(return_value=True), _send=AsyncMock(),
              _send_photo=AsyncMock(return_value=photo_ok),
              _link_hint=TelegramHandler._link_hint)


def _answer(data=PAYLOAD):
    out = {"reply_html": CARD_TEXT_HTML, "intent": "etf_flows"}
    if data is not None:
        out["data"] = data
    return out


class TestTheCommand:
    @pytest.mark.asyncio
    async def test_the_picture_is_sent_with_a_short_caption(self, monkeypatch):
        asked = []
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, *a, **k: asked.append(name) or _answer())
        h = _host()
        await TelegramHandler._cmd_etf(h, NS(), NS(args=[]))
        assert asked == ["etf_flows"]
        assert h._guard.await_args.args[1] == "etf"
        png, caption = h._send_photo.await_args.args[1], h._send_photo.await_args.args[2]
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert caption.split("\n") == [
            "📊 <b>US spot crypto ETF flows</b> — Sep 22 – Sep 28",
            "Net: <b>+$500.0M</b> across BTC, ETH",
            "<i>Source: SoSoValue · market data only</i>",
        ]
        assert h._send.await_count == 0

    @pytest.mark.asyncio
    async def test_the_picture_draws_the_payload_the_route_carried(self, monkeypatch):
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, *a, **k: _answer())
        seen = []
        monkeypatch.setattr(ec, "render_etf_card", lambda d: seen.append(d) or b"PNG")
        h = _host()
        await TelegramHandler._cmd_etf(h, NS(), NS(args=[]))
        assert seen == [PAYLOAD]
        assert h._send_photo.await_args.args[1] == b"PNG"

    @pytest.mark.asyncio
    async def test_a_card_with_no_payload_sends_the_text(self, monkeypatch):
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, *a, **k: _answer(data=None))
        h = _host()
        await TelegramHandler._cmd_etf(h, NS(), NS(args=[]))
        assert h._send_photo.await_count == 0
        sent = h._send.await_args.args[1]
        assert sent == wdp.web_card_text(_answer())
        assert "<br" not in sent and "SOL: not read" in sent

    @pytest.mark.asyncio
    async def test_a_picture_that_raised_sends_the_text_and_logs_only_the_class(self, monkeypatch):
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, *a, **k: _answer())

        def _boom(d):
            raise ValueError("SECRETVALUE in the payload")

        monkeypatch.setattr(ec, "render_etf_card", _boom)
        logged = []
        monkeypatch.setattr(mc, "system_log", NS(warning=lambda *a: logged.append(a)))
        h = _host()
        await TelegramHandler._cmd_etf(h, NS(), NS(args=[]))
        assert h._send_photo.await_count == 0
        assert h._send.await_args.args[1] == wdp.web_card_text(_answer())
        assert logged and "ValueError" in logged[0]
        assert not any("SECRETVALUE" in str(x) for row in logged for x in row)

    @pytest.mark.asyncio
    async def test_a_photo_telegram_refused_sends_the_text(self, monkeypatch):
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, *a, **k: _answer())
        h = _host(photo_ok=False)
        await TelegramHandler._cmd_etf(h, NS(), NS(args=[]))
        assert h._send_photo.await_count == 1
        assert h._send.await_args.args[1] == wdp.web_card_text(_answer())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer", [None, {"error": "Card unavailable"}, {"reply_html": ""}])
    async def test_a_channel_that_did_not_answer_gets_the_transports_sentence(self, monkeypatch, answer):
        monkeypatch.setattr(wdp, "fetch_web_card", lambda name, *a, **k: answer)
        h = _host()
        await TelegramHandler._cmd_etf(h, NS(), NS(args=[]))
        assert h._send_photo.await_count == 0
        assert h._send.await_args.args[1] == TelegramHandler._WEB_LINK_HINT

    @pytest.mark.asyncio
    async def test_the_gate_refusing_reads_nothing(self, monkeypatch):
        monkeypatch.setattr(wdp, "fetch_web_card",
                            lambda *a, **k: pytest.fail("a refused caller must not reach the fetch"))
        h = _host()
        h._guard = AsyncMock(return_value=False)
        await TelegramHandler._cmd_etf(h, NS(), NS(args=[]))
        assert h._send.await_count == 0 and h._send_photo.await_count == 0


# ── 5. the tables ────────────────────────────────────────────────────────────

class TestTheTables:
    def test_registered_guarded_and_in_the_catalogue(self):
        from bot.skills.command_catalog import all_entries
        src = inspect.getsource(TelegramHandler)
        assert '("etf", self._cmd_etf)' in src
        assert '@guard("etf")' in inspect.getsource(mc.MarketCommands._cmd_etf)
        title, audience, desc = all_entries()["etf"]
        assert title == "🌍 Market context" and audience == "user"
        assert "read-only" in desc

    def test_the_card_route_name_is_in_the_bots_fixed_set(self):
        assert "etf_flows" in WEB_CARDS

    def test_the_roles_that_hold_the_other_market_cards_hold_etf(self):
        for role in ("trader", "paper", "viewer"):
            assert "etf" in ROLE_PERMISSIONS[role], role
        assert "etf" not in ROLE_PERMISSIONS.get("pending", set())
