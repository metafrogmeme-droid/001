"""Leaderboard A2 — operator self-registration + public gateway serving.

The board goes live: the operator (opted in via PROOFOFPNL_LEADERBOARD_HANDLE)
registers its sealed statement, and the gateway serves the ranked, anonymous,
re-verifiable board with no auth.
"""
import inspect

from bot.proofofpnl import leaderboard as lb
from bot.proofofpnl.assemble import assemble_track_record
from bot.proofofpnl.publish import build_publication


def _trade(tid, side, price, ts):
    return {"id": tid, "order": tid, "symbol": "BTC/USDT:USDT", "side": side,
            "price": price, "amount": 1.0, "timestamp": ts,
            "fee": {"cost": 0.0, "currency": "USDT"}}


def _publication(ts=1_700_000_000):
    trades = [_trade("a1", "buy", 100.0, 1_700_000_001_000),
              _trade("a2", "sell", 120.0, 1_700_000_002_000),
              _trade("a3", "buy", 100.0, 1_700_000_003_000),
              _trade("a4", "sell", 95.0, 1_700_000_004_000)]
    bundle = assemble_track_record(trades, account_ids=["acct"],
                                   range_start=ts - 1000, range_end=ts)
    return build_publication(bundle, published_at_ts=ts)


def test_gateway_serves_ranked_anonymous_board(monkeypatch, tmp_path):
    monkeypatch.setenv("PROOFOFPNL_LEADERBOARD_PATH", str(tmp_path / "board.json"))
    lb.reset_leaderboard_registry()
    lb.get_leaderboard_registry().put("skywalker", _publication())

    from bot.web import user_gateway
    payload = user_gateway._leaderboard_payload()

    assert payload["format"] == "runeclaw.proofofpnl.leaderboard.v0"
    assert payload["count"] == 1
    row = payload["rows"][0]
    assert row["handle"] == "skywalker" and row["rank"] == 1 and row["verified"] is True
    # Anonymous + size-agnostic: no dollar figure ever crosses the wire.
    for leaky in ("net_pnl", "fees", "max_dd", "balance"):
        assert leaky not in row
    lb.reset_leaderboard_registry()


def test_public_route_is_registered_no_auth():
    src = inspect.getsource(__import__("bot.web.user_gateway", fromlist=["x"]))
    assert 'add_get("/public/leaderboard", handle_leaderboard_public)' in src
    # The public handler must not go through the per-user guard.
    h = inspect.getsource(
        __import__("bot.web.user_gateway", fromlist=["x"]).handle_leaderboard_public)
    assert "_guard_user" not in h


def test_engine_registers_operator_only_when_opted_in():
    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine._maybe_publish_proofofpnl)
    assert "PROOFOFPNL_LEADERBOARD_HANDLE" in src        # opt-in gate
    assert "get_leaderboard_registry" in src
    # Registration is gated on a non-empty handle (default OFF) and fail-open.
    assert "if handle:" in src
    assert "pub is not None" in src


class TestAFailedReadIsNotAnEmptyBoard:
    """The board swallowed every exception into ``rows = []`` and returned 200,
    so a crashed registry read arrived at /leaderboard as "0 verified agents" —
    a confident negative built from an unreadable source.

    What made it survive review is that every consumer already handles the
    honest case and none of them could ever see it: the proxy relays non-2xx
    and refuses to cache it, the page swaps in "temporarily unavailable", and
    board_watch bails instead of announcing that the whole board moved. The
    error path was wired end to end and unreachable.

    RED HERRING: an unknown season legitimately ranks empty. That is a real
    measurement of a real window and must stay a 200 — the fix has to
    distinguish "nothing qualified" from "nothing could be read", which is the
    entire distinction the old code erased.
    """

    def _handler(self, monkeypatch, exc=None):
        import asyncio
        import types
        from bot.web import user_gateway

        if exc is not None:
            def _boom(*_a, **_k):
                raise exc
            monkeypatch.setattr(user_gateway, "_leaderboard_payload", _boom)
        req = types.SimpleNamespace(query={})
        return asyncio.run(user_gateway.handle_leaderboard_public(req))

    def test_an_unreadable_board_answers_503(self, monkeypatch):
        resp = self._handler(monkeypatch, exc=OSError("disk gone"))
        assert resp.status == 503

    def test_the_reason_is_a_coarse_code_not_the_driver_message(self, monkeypatch):
        """Unauthenticated endpoint: the exception text can name paths, hosts
        or config. Same rule /readyz follows."""
        resp = self._handler(monkeypatch,
                             exc=OSError("/srv/secret/board.json: denied"))
        body = resp.body.decode()
        assert "leaderboard_unavailable" in body
        for leak in ("/srv", "secret", "denied", "OSError"):
            assert leak not in body

    def test_a_genuinely_empty_board_still_answers_200(self, monkeypatch, tmp_path):
        """The red herring. Nobody qualifying is a reading, not a failure."""
        monkeypatch.setenv("PROOFOFPNL_LEADERBOARD_PATH",
                           str(tmp_path / "empty.json"))
        lb.reset_leaderboard_registry()
        try:
            resp = self._handler(monkeypatch)
            assert resp.status == 200
            assert b'"rows": []' in resp.body or b'"rows":[]' in resp.body
        finally:
            lb.reset_leaderboard_registry()

    def test_a_missing_season_index_does_not_take_the_board_down(self, monkeypatch,
                                                                 tmp_path):
        """The tab strip is chrome — losing it hides no measurement, so it
        degrades on its own rather than 503-ing a board that reads fine."""
        monkeypatch.setenv("PROOFOFPNL_LEADERBOARD_PATH", str(tmp_path / "b.json"))
        lb.reset_leaderboard_registry()
        lb.get_leaderboard_registry().put("skywalker", _publication())
        from bot.proofofpnl import seasons
        from bot.web import user_gateway
        store = seasons.get_season_store()
        monkeypatch.setattr(type(store), "season_ids",
                            lambda _s: (_ for _ in ()).throw(OSError("index")))
        try:
            payload = user_gateway._leaderboard_payload()
            assert payload["count"] == 1        # the measurement survived
            assert payload["seasons"] == []     # the chrome did not
        finally:
            lb.reset_leaderboard_registry()
