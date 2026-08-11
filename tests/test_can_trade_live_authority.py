"""
_can_trade_live is THE single authority for 'may this Telegram user place LIVE
orders'. It is defense-in-depth: BOTH the operator env allowlist AND the per-user
UserStore flag must permit it. Centralizing the 7 former scattered reads here means
every gate and status display agree, and a stale users.json flag can never let a
non-allowlisted user trade live.
"""

import dataclasses

import bot.config as cfg_mod
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.skills.telegram_handler import TelegramHandler


def _handler():
    return TelegramHandler(RuneClawEngine())


def _with_allowlist(monkeypatch, chat_id="", admin_ids="", open_live=False):
    """Config for the STAGED-ROLLOUT policy by default.

    These cases predate the open policy and describe the allowlist-plus-flag
    rule, so they select it explicitly rather than inheriting whatever the env
    happens to set — a suite that changes meaning with an env var is a suite
    that stops testing what its names claim.
    """
    new_tg = dataclasses.replace(CONFIG.telegram, chat_id=chat_id, admin_ids=admin_ids)
    new_cfg = dataclasses.replace(CONFIG, telegram=new_tg,
                                  live_open_to_key_holders=open_live)
    monkeypatch.setattr(cfg_mod, "CONFIG", new_cfg)
    # telegram_handler reads CONFIG at call time via the module global.
    monkeypatch.setattr("bot.skills.telegram_handler.CONFIG", new_cfg, raising=False)
    # engine.CONFIG too: _can_trade_live now asks engine._is_operator_user who
    # the operator is, and that reads the engine module's own CONFIG. Patching
    # only one of the two makes the halves of a single decision disagree about
    # which config they are reading.
    monkeypatch.setattr("bot.core.engine.CONFIG", new_cfg, raising=False)
    return new_cfg


class TestCanTradeLiveAuthority:
    def test_allowlisted_user_with_flag_can_trade(self, monkeypatch):
        h = _handler()
        _with_allowlist(monkeypatch, chat_id="111")
        h.users.register(111, name="op")
        h.users.set_live_trading(111, True)
        assert h._can_trade_live(111) is True

    def test_non_allowlisted_user_blocked_even_with_stale_flag(self, monkeypatch):
        # The divergence edge, restated for the open policy. What must never
        # happen is a non-operator placing orders on the OPERATOR's account.
        # The mechanism moved — from "on the env allowlist" to "brings their
        # own keys" — but the invariant did not: a stranger with a stale flag
        # and NO KEYS still gets no. With PER_USER_LIVE_ENABLED off (as here),
        # every order would route to the operator, so the answer is no even if
        # they had keys.
        h = _handler()
        _with_allowlist(monkeypatch, chat_id="111")
        h.users.register(222, name="stranger")
        h.users.set_live_trading(222, True)   # stale/leftover flag
        assert h._can_trade_live(222) is False

    def test_allowlisted_but_no_flag_cannot_trade(self, monkeypatch):
        h = _handler()
        _with_allowlist(monkeypatch, chat_id="111,222")
        h.users.register(222, name="viewer")  # no live flag granted
        assert h._can_trade_live(222) is False

    def test_empty_allowlist_falls_back_to_flag(self, monkeypatch):
        # Demo/paper (no allowlist configured) → identical to the prior
        # behaviour: the UserStore flag alone decides. Pinned with the staged
        # rollout selected, because that fallback is part of the OLD policy —
        # under the open policy an unset allowlist no longer implies a single
        # operator account, and TestLiveOpensToKeyHoldersNotToEveryone pins what
        # replaces it.
        h = _handler()
        _with_allowlist(monkeypatch, chat_id="", admin_ids="",
                        open_live=False)
        h.users.register(333, name="demo")
        assert h._can_trade_live(333) is False
        h.users.set_live_trading(333, True)
        assert h._can_trade_live(333) is True

    def test_accepts_int_or_str_id(self, monkeypatch):
        h = _handler()
        _with_allowlist(monkeypatch, chat_id="444")
        h.users.register(444, name="op")
        h.users.set_live_trading(444, True)
        assert h._can_trade_live(444) is True
        assert h._can_trade_live("444") is True


class TestLiveOpensToKeyHoldersNotToEveryone:
    """"Live trade for all traders" means every trader who brings their OWN
    account. The thing that must stay impossible is a regular user's order
    filling on the operator's balance — and with PER_USER_LIVE_ENABLED off that
    is exactly where every order goes, because `_executor_for` returns the
    shared executor and `per_user_live_eligibility` is a documented no-op.

    So the master switch is enforced HERE rather than left to two env vars being
    kept consistent by hand. The states below are the four corners of that.
    """

    def _cfg(self, monkeypatch, *, per_user, open_live, chat_id="111"):
        import dataclasses
        import bot.config as cfg_mod
        from bot.config import CONFIG
        new_tg = dataclasses.replace(CONFIG.telegram, chat_id=chat_id, admin_ids="")
        new_cfg = dataclasses.replace(CONFIG, telegram=new_tg,
                                      per_user_live_enabled=per_user,
                                      live_open_to_key_holders=open_live)
        monkeypatch.setattr(cfg_mod, "CONFIG", new_cfg)
        monkeypatch.setattr("bot.skills.telegram_handler.CONFIG", new_cfg, raising=False)
        monkeypatch.setattr("bot.core.engine.CONFIG", new_cfg, raising=False)
        return new_cfg

    def _keys(self, monkeypatch, holders):
        import types
        fake = types.SimpleNamespace(get=lambda uid: ("k" if str(uid) in holders else None))
        monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                            lambda: fake)

    def test_a_key_holder_may_trade_live_without_any_admin_step(self, monkeypatch):
        h = _handler()
        self._cfg(monkeypatch, per_user=True, open_live=True)
        self._keys(monkeypatch, {"222"})
        h.users.register(222, name="trader")
        assert h._can_trade_live(222) is True

    def test_a_trader_with_no_keys_may_not(self, monkeypatch):
        """The whole point: no keys means their order would land on the
        operator's account, so the answer is no."""
        h = _handler()
        self._cfg(monkeypatch, per_user=True, open_live=True)
        self._keys(monkeypatch, set())
        h.users.register(333, name="keyless")
        assert h._can_trade_live(333) is False

    def test_the_master_switch_off_refuses_every_non_operator(self, monkeypatch):
        """PER_USER_LIVE_ENABLED off routes EVERY order to the operator account.
        Keys or not, a non-operator must be refused — the misconfiguration is
        made unreachable rather than merely discouraged."""
        h = _handler()
        self._cfg(monkeypatch, per_user=False, open_live=True)
        self._keys(monkeypatch, {"222"})
        h.users.register(222, name="trader")
        assert h._can_trade_live(222) is False

    def test_an_explicit_revoke_beats_owning_keys(self, monkeypatch):
        h = _handler()
        self._cfg(monkeypatch, per_user=True, open_live=True)
        self._keys(monkeypatch, {"444"})
        h.users.register(444, name="banned")
        h.users.set_live_trading(444, False)
        assert h._can_trade_live(444) is False

    def test_never_granted_is_not_revoked(self, monkeypatch):
        """`register()` writes `can_trade_live: False` on EVERY new account as
        the paper-only default. The first implementation read that as a revoke,
        which would have banned the entire user base the moment live opened —
        this test caught it. A default and a decision are different facts."""
        h = _handler()
        h.users.register(555, name="fresh")
        assert h.users.get(555).get("can_trade_live") is False   # the default
        assert h.users.live_trading_revoked(555) is False        # not a decision
        assert h.users.can_trade_live(555) is False

    def test_a_revoke_is_revoked(self):
        h = _handler()
        h.users.register(556, name="banned")
        h.users.set_live_trading(556, False)
        assert h.users.live_trading_revoked(556) is True

    def test_a_revoke_can_be_lifted(self):
        h = _handler()
        h.users.register(557, name="pardoned")
        h.users.set_live_trading(557, False)
        h.users.set_live_trading(557, True)
        assert h.users.live_trading_revoked(557) is False

    def test_the_revoke_survives_a_reload(self, tmp_path):
        """A ban held only in memory lifts itself on the next restart — the
        failure mode that started this whole registration thread."""
        from bot.utils.user_store import UserStore
        path = str(tmp_path / "users.json")
        s1 = UserStore(path)
        s1.register(558, name="banned")
        s1.set_live_trading(558, False)
        assert UserStore(path).live_trading_revoked(558) is True

    def test_an_unreadable_credential_store_denies(self, monkeypatch):
        """Fail-closed: this decides whether real money moves, and a filled
        order is not recoverable."""
        h = _handler()
        self._cfg(monkeypatch, per_user=True, open_live=True)

        def _boom():
            raise OSError("keystore unreadable")
        monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store", _boom)
        h.users.register(666, name="trader")
        assert h._can_trade_live(666) is False

    def test_the_staged_rollout_still_works_when_the_switch_is_off(self, monkeypatch):
        """LIVE_OPEN_TO_KEY_HOLDERS off restores /grant_live as the gate."""
        h = _handler()
        # Empty allowlist so the ALLOWLIST is not the thing doing the blocking
        # — the claim under test is that linked keys alone are not enough.
        self._cfg(monkeypatch, per_user=True, open_live=False, chat_id="")
        self._keys(monkeypatch, {"777"})
        h.users.register(777, name="trader")
        assert h._can_trade_live(777) is False      # keys alone are not enough
        h.users.set_live_trading(777, True)
        assert h._can_trade_live(777) is True

    def test_a_web_identity_is_still_structurally_paper_only(self, monkeypatch):
        h = _handler()
        self._cfg(monkeypatch, per_user=True, open_live=True)
        self._keys(monkeypatch, {"web:9"})
        assert h._can_trade_live("web:9") is False
