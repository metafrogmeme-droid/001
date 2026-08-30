"""Deleting an account has to reach the bot, and it has to report per store.

THE KEYS ARE HERE, NOT IN MYSQL. A user's exchange API credentials live in
this process's encrypted vault; their agent profile, their user record and
their language preference live here too. So "delete my account" carried out
only against the web database leaves the bot holding the credentials that move
real money, under a message telling the person their account is gone. That is
this repository's central defect — a confident report of a state that is not
true — applied to the one surface where it costs money rather than a wrong
number on a card.

TWO HALVES, AND THEY FAIL DIFFERENTLY.

`UserStore.forget` was missing outright. `revoke` sets a record to pending and
drops its admission, which is the right answer for withdrawing access and the
wrong one for erasure: telegram id, name, referrer, language and every
timestamp survive it. There was no delete in the store at all, which is half
of why the product had no deletion path.

`handle_account_purge` answers PER STORE. "deleted", "there was nothing to
delete" and "that store raised" are three different facts, and a blanket
`{"ok": true}` would flatten the third into the first — the exact shape of
`except Exception: pass  # not critical` that CLAUDE.md spends a section on.
The web half refuses to erase anything unless every store came back resolved,
so the distinction is not decorative: it is what the abort is decided on.
"""
from __future__ import annotations

import json

import pytest

from bot.utils.user_store import UserStore


def _store(tmp_path, ids=("111", "222")):
    path = tmp_path / "users.json"
    path.write_text(json.dumps(
        {i: {"name": f"u{i}", "role": "trader", "tier": "basic",
             "authorized": True, "language": "en"} for i in ids}),
        encoding="utf-8")
    return UserStore(str(path))


class TestForgetActuallyForgets:

    def test_the_record_is_gone_from_memory_and_from_disk(self, tmp_path):
        store = _store(tmp_path)
        assert store.forget("111") is True
        assert store.get("111") is None
        # Read the file back rather than trusting the object: a forget that
        # does not persist is undone by the next restart, which is the worst
        # kind of "deleted" — it looks right until it does not.
        on_disk = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))
        assert "111" not in on_disk
        assert "222" in on_disk, "forgetting one user removed another"

    def test_forgetting_an_unknown_id_is_false_not_an_error(self, tmp_path):
        # `none` and `deleted` are different answers and the gateway reports
        # both. A raise here would be reported as `error` and would abort a
        # deletion for an account the bot simply never knew.
        store = _store(tmp_path)
        assert store.forget("999") is False

    def test_forget_takes_an_int_as_well_as_a_str(self, tmp_path):
        # Telegram ids arrive as ints from the bot and as strings from the
        # gateway. A store keyed by str that silently misses on int would
        # answer `none` — "there was nothing to delete" — for a record that is
        # sitting right there.
        store = _store(tmp_path)
        assert store.forget(222) is True
        assert store.get("222") is None

    def test_revoke_is_not_erasure(self, tmp_path):
        """The reason `forget` had to exist.

        Stated as a test rather than a comment because the two are one word
        apart at a call site, and picking the wrong one leaves a full record
        behind while looking like a deletion.
        """
        store = _store(tmp_path)
        store.revoke("111")
        left = store.get("111")
        assert left is not None, "revoke deleted the record — then forget is redundant"
        assert left.get("name") == "u111", (
            "revoke used to keep the identifying fields; if it no longer does, "
            "this test and the deletion path both need rereading")


class TestThePurgeEndpointReportsPerStore:
    """`handle_account_purge` is what the web half decides on."""

    def _module(self):
        return pytest.importorskip("bot.web.user_gateway")

    def test_it_is_registered_as_a_route(self):
        """WIRING, deliberately. A handler nothing routes to is the shape
        CLAUDE.md calls out twice — a card built and never reached, a whole
        subsystem imported by nothing. The web half would get a 404 and abort
        every deletion, which fails safe and fails always."""
        import inspect

        src = inspect.getsource(self._module())
        assert '"/account/purge", handle_account_purge' in src, (
            "the purge handler is not routed; the DELETE route in app/auth.js "
            "would 502 on every account")

    def test_every_store_answers_and_the_answers_are_returned(self):
        src = __import__("inspect").getsource(self._module().handle_account_purge)
        for store in ("exchange_credentials", "agent_profile", "agent_memory",
                      "user_record"):
            assert f'result["{store}"]' in src, (
                f"{store} is not reported separately, so a failure in it is "
                "indistinguishable from success")
        assert '"stores": result' in src, "the per-store detail never reaches the caller"

    def test_a_raising_store_is_error_not_silence(self):
        """`except Exception: pass` here would turn a vault that refused to
        delete into a purge that reported success."""
        src = __import__("inspect").getsource(self._module().handle_account_purge)
        assert src.count('= "error"') >= 4, (
            "not every store maps its exception onto a reported failure")
        assert 'all(v in ("deleted", "none") for v in result.values())' in src, (
            "the rollup no longer requires every store to have resolved — an "
            "`error` would count as a purge")

    def test_the_failure_handlers_log_through_a_name_that_exists(self):
        """The first version called `log.warning` in all three except blocks
        and `log` is not defined in that module.

        Every one of them carries `# pragma: no cover - defensive`, so nothing
        drove them: a store that raised would have hit a NameError INSIDE the
        handler written to catch it, and the per-store `error` report — the
        whole reason the endpoint answers store by store — would have become a
        500 saying only "something". Caught by ruff F821 in preflight, which is
        why that gate runs before a push and not after one.
        """
        import inspect

        mod = self._module()
        src = inspect.getsource(mod.handle_account_purge)
        for name in {m for m in __import__("re").findall(r"^\s+(\w+)\.warning", src,
                                                         __import__("re").M)}:
            assert hasattr(mod, name), (
                f"the purge handler logs through `{name}`, which does not exist "
                "in bot/web/user_gateway.py — the except block raises instead of "
                "reporting")

    def test_a_partial_purge_is_409_and_not_200(self):
        src = __import__("inspect").getsource(self._module().handle_account_purge)
        assert "status=200 if ok else 409" in src, (
            "a partial purge answers 200, so the web half deletes the account "
            "over a bot that is still holding credentials")


class TestNoPerUserStoreOutlivesTheDeletePath:
    """The purge listed three stores and a fourth had just started recording.

    `user_memory_store` began keeping what the agent had actually looked at for
    a person — genuinely useful, and invisible to a deletion that only knew
    about the profile they typed. A purge that deletes what someone wrote and
    keeps what was watched has not forgotten them; it has kept the half they
    never chose to write down.

    Enumerated rather than listed, so the NEXT per-user store cannot be
    forgotten either. A module in bot/core with a `clear(user_id)` is, by that
    signature, holding something keyed to a person — if the purge does not name
    it, either wire it or say here why it does not belong.
    """

    #: Modules whose `clear(user_id)` is deliberately NOT part of an account
    #: purge. Empty today. An entry here needs a reason, not a name.
    EXEMPT: dict = {}

    def _per_user_stores(self):
        import ast
        import pathlib
        found = []
        for f in sorted(pathlib.Path("bot/core").glob("*.py")):
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except SyntaxError:                   # pragma: no cover - defensive
                continue
            for node in tree.body:
                if (isinstance(node, ast.FunctionDef) and node.name == "clear"
                        and node.args.args
                        and node.args.args[0].arg in ("user_id", "uid")):
                    found.append(f.stem)
        return found

    def test_the_sweep_finds_the_stores_it_is_meant_to(self):
        # An empty sweep passes the assertion below over nothing at all —
        # absent read as clean, one more time.
        stores = self._per_user_stores()
        assert "user_profile_store" in stores and "user_memory_store" in stores, (
            f"the per-user store sweep found {stores}; it is broken, not empty")

    def _aliases(self, module_name):
        """`from bot.core import user_profile_store as _profile_store`.

        The handler names the ALIAS, so a scan for the module name alone
        reports a store that is purged on the very first line it checks — the
        checker manufacturing the accusation it exists to prevent, which this
        repo has now watched happen three times.
        """
        import ast
        import inspect

        mod = pytest.importorskip("bot.web.user_gateway")
        names = {module_name}
        for node in ast.walk(ast.parse(inspect.getsource(mod))):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == module_name and a.asname:
                        names.add(a.asname)
        return names

    def test_every_per_user_store_is_named_by_the_purge(self):
        import inspect

        mod = pytest.importorskip("bot.web.user_gateway")
        src = inspect.getsource(mod.handle_account_purge)
        missed = [s for s in self._per_user_stores()
                  if s not in self.EXEMPT
                  and not any(a in src for a in self._aliases(s))]
        assert not missed, (
            "these hold per-user state and the account purge does not touch "
            f"them: {missed}\n\nWire each into handle_account_purge, or add it "
            "to EXEMPT with the reason it is not personal data.")
