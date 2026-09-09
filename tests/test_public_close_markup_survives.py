"""The public close card printed its own tags to the channel.

Live, 2026-09-09:

    📉 TRADE CLOSED
    ──────────────────

    🔴 <b>CLUSDT</b> LONG closed (leverage overshoot)
    Move: <code>-0.08%</code> | on margin <code>-1.67%</code> | Hold: <code>1.0h</code>

`public_close_line` deliberately emits `<b>` and `<code>`, and
`post_trade_closed` ran `html.escape()` over the finished string, so every tag
arrived as `&lt;b&gt;` and Telegram rendered it literally.

WHY THE ESCAPE WAS THERE, because it was not gratuitous. The forwarder takes
two DIFFERENT kinds of message:

    public_close_line(close_data)  -> HTML
    close_msg (live_executor)      -> PLAIN TEXT, no tags at all

Escaping is correct for the second and wrong for the first, and by the time
the string reaches `post_trade_closed` nothing can tell a tag the producer
wrote from a bracket a venue supplied. So the boundary moved outward rather
than away: `public_close_line` escapes the three fields it interpolates (`sym`,
`direction`, `reason` — none were escaped, so a venue symbol carrying `<` would
have broken the parse), `alerts_monitor` escapes the plain-text fallback where
it picks it, and the forwarder escapes nothing because both callers hand it
ready HTML.

`tests/test_marketing_public_no_dollars.py` already drove this exact path —
`post_trade_closed(public_close_line(...))`, asserting on the sent message —
and checked only the emoji. That one missing assertion is the whole distance
between a green suite and tags on a public channel, and it lives there now
too.
"""

import asyncio

import pytest

from bot.marketing.public_text import public_close_line, scrub_money


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id=None, text=None, **kw):
        self.sent.append(text)


def _forwarder(tmp_path, monkeypatch):
    import bot.marketing.channel_forwarder as cf
    monkeypatch.setattr(cf, "_CONFIG_PATH", tmp_path / "channel.json")
    f = cf.ChannelForwarder()
    f.set_bot(_Bot())
    f.add_group(-100123)
    return f


def _post(tmp_path, monkeypatch, message):
    f = _forwarder(tmp_path, monkeypatch)
    asyncio.run(f.post_trade_closed(message))
    assert f._bot.sent, "nothing was posted"
    return f._bot.sent[0]


#: The card that leaked, field for field.
CLUSDT = {"symbol": "CLUSDT", "direction": "LONG",
          "reason": "leverage_overshoot", "pnl_pct": -0.08,
          "pnl_pct_margin": -1.67, "hold_time": "1.0h"}


class TestTheLiveCard:
    def test_the_tags_reach_the_channel_as_tags(self, tmp_path, monkeypatch):
        sent = _post(tmp_path, monkeypatch, public_close_line(CLUSDT))
        assert "<b>CLUSDT</b>" in sent
        assert "<code>-0.08%</code>" in sent

    def test_no_escaped_tag_reaches_the_channel(self, tmp_path, monkeypatch):
        sent = _post(tmp_path, monkeypatch, public_close_line(CLUSDT))
        assert "&lt;b&gt;" not in sent
        assert "&lt;code&gt;" not in sent
        assert "&lt;" not in sent, "something is still being double-escaped"

    def test_the_reason_still_reads_as_words(self, tmp_path, monkeypatch):
        sent = _post(tmp_path, monkeypatch, public_close_line(CLUSDT))
        assert "leverage overshoot" in sent

    def test_the_producer_alone_is_already_correct(self):
        """The forwarder is not the only thing that could regress this."""
        line = public_close_line(CLUSDT)
        assert "<b>CLUSDT</b>" in line
        assert "&lt;" not in line


class TestAHostileFieldIsStillEscaped:
    """The escape guarded something real. It must keep guarding it."""

    @pytest.mark.parametrize("field,value", [
        ("symbol", "<script>alert(1)</script>"),
        ("direction", "LONG<b>"),
        ("reason", "closed <b>early</b>"),
    ])
    def test_it_cannot_inject_a_tag(self, field, value, tmp_path, monkeypatch):
        data = dict(CLUSDT)
        data[field] = value
        line = public_close_line(data)
        assert line is not None
        sent = _post(tmp_path, monkeypatch, line)
        # The card's OWN tags are still tags; the injected one is not.
        assert "<b>" in sent
        assert "<script>" not in sent
        assert "<b>early</b>" not in sent
        assert "&lt;" in sent, f"{field} was interpolated raw"

    def test_an_ampersand_reason_is_not_cut_mid_entity(self):
        """`reason` is truncated at 48. Truncate first, escape second.

        The other order slices `&amp;` into `&am`, which is malformed markup
        rather than a shortened word.
        """
        data = dict(CLUSDT)
        data["reason"] = "risk & margin " + "x" * 60
        line = public_close_line(data)
        assert "&am " not in line and "&am)" not in line
        assert "&amp;" in line


class TestThePlainTextFallbackIsEscapedByItsCaller:
    """`public_close_line` answers None when the record cannot tell the close.

    The forwarder then receives `live_executor`'s close card, which carries no
    tags at all. Now that the forwarder escapes nothing, escaping that is the
    caller's job — and `alerts_monitor` is where the choice is made, so that is
    where it happens.
    """

    def test_the_line_declines_when_the_percent_is_missing(self):
        assert public_close_line({"symbol": "BTC", "direction": "LONG"}) is None

    def test_the_caller_escapes_the_fallback(self):
        import inspect

        from bot.skills import alerts_monitor
        src = inspect.getsource(alerts_monitor)
        assert "public_close_line(close_data) or html.escape(msg)" in src, (
            "the plain-text fallback reaches a ready-HTML sink unescaped")

    def test_an_escaped_plain_card_survives_the_post(
            self, tmp_path, monkeypatch):
        import html
        plain = ("CLOSED LONG TRX/USDT (leverage_overshoot)\n"
                 "Fill source: ticker_fallback")
        sent = _post(tmp_path, monkeypatch, html.escape(plain))
        assert "CLOSED LONG TRX/USDT" in sent
        assert "leverage_overshoot" in sent


class TestSection4StillHolds:
    """Dropping an escape must not drop the dollar rule with it."""

    def test_a_dollar_amount_does_not_reach_the_public_card(
            self, tmp_path, monkeypatch):
        sent = _post(tmp_path, monkeypatch,
                     "CLOSED LONG BTC/USDT\nPnL: +$12.3456 (+1.23%)")
        assert "12.3456" not in sent
        assert "+1.23%" in sent

    def test_the_scrubber_is_still_reached(self):
        out, removed = scrub_money("PnL: +$12.3456 (+1.23%) | Hold: 42m")
        assert removed == 1
        assert "$" not in out

    def test_the_structured_line_carries_no_dollars_by_construction(self):
        line = public_close_line(CLUSDT)
        assert "$" not in line
