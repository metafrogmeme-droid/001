"""`/vault` tells the operator what to run. Every instruction must be one.

The card's job is to name, per managed secret, the command that protects it —
its own docstring says it "is how you verify nothing is left unprotected". It
decided that by asking whether the key's NAME ends `_API_KEY`, which is a guess
about what a key is FOR, not a reading of what any command DOES. Four keys took
that route and none of them can be set that way:

    ONCHAIN_API_KEY       a Glassnode / Arkham / Nansen key (docs/ONCHAIN.md)
    HYPERLIQUID_API_KEY   an exchange key
    LLM_API_KEY           the generic default; no invocation of /setllm writes it
    XAI_API_KEY           the command RAN, said "LLM provider updated", stored nothing

The last is the expensive one and it was not a card bug at all. `/setllm`
carried a hand-written 10-row copy of `bot.llm.provider._PROVIDER_KEY_ENV`
(11 rows), missing `grok`. So the key went into the runtime config, never into
the vault, under a help text promising it is "stored ENCRYPTED in the operator
vault — they survive restarts and redeploys". Free-user chat routes to Grok and
falls back "if XAI_API_KEY is unset" (provider.py), so it fell back on every
restart, and `/vault` reported the key missing however many times it was set.

`provider.py`'s own `set_provider` carries the scar of the FIRST copy of that
map — "was a local copy of 7 of the 11". This file is the ratchet that stops a
fourth: the two directions below make the card's instructions and the command's
capabilities check each other, so neither can drift alone.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.core.secrets_vault import _DEFAULT_MANAGED
from bot.llm.provider import _PROVIDER_KEY_ENV, LLMProvider, provider_key_env, settable_key_envs
from bot.skills.account_commands import optional_venue_absences, vault_fix_hint

SETLLM = "/setllm <provider> <key>"


class TestTheMapHasOneCopy:
    def test_every_provider_that_takes_a_key_has_an_env_var(self):
        """The row that was missing was `grok`, and nothing noticed."""
        from bot.llm.provider import _KEYLESS_PROVIDERS
        for p in LLMProvider:
            if p in _KEYLESS_PROVIDERS or p is LLMProvider.CUSTOM:
                continue
            assert provider_key_env(p), f"{p.value} has no key env var"

    def test_grok_resolves_to_the_vault_slot_that_exists_for_it(self):
        """XAI_API_KEY is vault-managed; the command has to be able to fill it."""
        assert provider_key_env("grok") == "XAI_API_KEY"
        assert "XAI_API_KEY" in _DEFAULT_MANAGED

    def test_it_takes_a_string_or_an_enum(self):
        assert provider_key_env("anthropic") == provider_key_env(LLMProvider.ANTHROPIC)

    def test_an_unknown_provider_is_empty_not_a_raise(self):
        """The caller's `if _key_env:` must see a falsy answer, not an exception."""
        assert provider_key_env("not-a-provider") == ""
        assert provider_key_env("") == ""

    def test_a_keyless_provider_stores_nothing(self):
        """/setllm ollama takes no key; an env var here would invent one."""
        assert provider_key_env("ollama") == ""

    def test_setllm_reads_the_map_rather_than_repeating_it(self):
        """The defect was a second copy. Assert there is no second copy.

        A dict literal of provider names inside the command is the shape that
        went wrong twice; `provider_key_env` is the only sanctioned reader.
        """
        import inspect

        from bot.skills.llm_commands import LLMCommands
        src = inspect.getsource(LLMCommands._cmd_setllm)
        assert "provider_key_env(" in src
        for name in ("anthropic", "openai", "gemini", "deepseek"):
            assert f'"{name}":' not in src, (
                f"a local provider->env map is back ({name})")


class TestSetllmActuallyStoresTheKey:
    """The map was the cause; THIS is the claim. Drive the command.

    Everything above is about a table. What went wrong in production is that a
    key entered by an operator did not arrive in the vault, so the test that
    matters plants the command and reads what `store_secrets` was handed.
    """

    @pytest.fixture
    def run_setllm(self, monkeypatch):
        from bot.llm.provider import BYOK
        from bot.skills import llm_commands

        stored: dict = {}

        def _store(mapping):
            stored.update(mapping)

        monkeypatch.setattr("bot.core.secrets_vault.store_secrets", _store)
        # `anthropic` is the ONE provider with a live key preflight ahead of
        # the store, and it returns early on INVALID. Without this stub the
        # parametrised case below exercises the rejection path — and would
        # have passed had it asserted "nothing stored", for a reason that has
        # nothing to do with the map under test.
        from bot.llm import key_health as _kh
        monkeypatch.setattr(_kh, "validate_anthropic_key",
                            lambda *a, **k: (_kh.VALID, ""))
        # set_provider does live client construction; the map is what is under
        # test, so accept the provider and let the storage decision run.
        monkeypatch.setattr(BYOK, "set_provider",
                            lambda *a, **k: (True, "ok"), raising=False)

        async def _run(provider, key):
            sent: list[str] = []
            h = llm_commands.LLMCommands.__new__(llm_commands.LLMCommands)

            async def _send(update, text, *a, **k):
                sent.append(str(text))

            h._send = _send
            h._is_admin = lambda update: True
            h._lang = lambda update: "en"
            h.engine = SimpleNamespace()
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=1),
                effective_chat=SimpleNamespace(id=1, type="private"),
                message=SimpleNamespace(delete=AsyncMock()), callback_query=None)
            ctx = SimpleNamespace(args=[provider, key])
            await h._cmd_setllm(update, ctx)
            return stored, sent

        return _run

    @pytest.mark.asyncio
    async def test_grok_reaches_the_vault(self, run_setllm):
        """It reported success and stored nothing, on every restart."""
        stored, sent = await run_setllm("grok", "xai-secret-value")
        assert stored.get("XAI_API_KEY") == "xai-secret-value", (
            "/setllm grok answered the operator and wrote nothing to the vault")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider,env_var", sorted(
        (p.value, e) for p, e in _PROVIDER_KEY_ENV.items()))
    async def test_every_provider_in_the_map_reaches_its_slot(
            self, run_setllm, provider, env_var):
        """One row was missing and nothing noticed. Check every row."""
        stored, _ = await run_setllm(provider, "k-" + provider)
        assert stored.get(env_var) == "k-" + provider

    @pytest.mark.asyncio
    async def test_a_keyless_provider_stores_nothing(self, run_setllm):
        """`if _key_env:` must still decline; ollama has no key to keep."""
        stored, _ = await run_setllm("ollama", "")
        assert stored == {}


class TestNoSurfaceDerivesTheEnvNameInstead:
    """A name DERIVED from the provider's own is a fourth copy with extra steps.

    `shadow_eval` read `f"{PROVIDER}_API_KEY"`. That is right for ten of the
    eleven providers, which is exactly why nobody noticed: any derivation looks
    correct until you check the one exception. Grok's key is in `XAI_API_KEY`,
    so shadow eval on Grok read a variable that does not exist, got "", and
    logged "could not build client" with the key sitting in the process.
    """

    def test_the_derivation_is_wrong_for_exactly_one_provider(self):
        """State the fact the fix rests on, so a rename cannot quietly undo it."""
        missed = [p.value for p, real in _PROVIDER_KEY_ENV.items()
                  if real not in (f"{p.value.upper()}_LLM_API_KEY",
                                  f"{p.value.upper()}_API_KEY")]
        assert missed == ["grok"], missed

    @staticmethod
    def _resolved_key(monkeypatch, provider, env):
        """The api_key shadow eval hands its client builder, for `provider`.

        A first draft called `_client_for(analyzer, provider, model)` — a
        method that does not exist, with an argument list the real one does not
        take. It is `_resolve(analyzer)`, and the provider comes from
        LLM_SHADOW_PROVIDER. Read the definition; a name you remember is not a
        measurement.
        """
        from bot.llm.shadow_eval import ShadowEval

        for e in list(_PROVIDER_KEY_ENV.values()) + ["LLM_SHADOW_API_KEY"]:
            monkeypatch.delenv(e, raising=False)
        monkeypatch.delenv(f"{provider.upper()}_LLM_API_KEY", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("LLM_SHADOW_PROVIDER", provider)
        monkeypatch.setenv("LLM_SHADOW_MODEL", "some-model")

        seen: dict = {}
        ev = ShadowEval.__new__(ShadowEval)
        ev._client, ev._cfg, ev._client_key = None, None, ""

        class _Analyzer:
            @staticmethod
            def _build_client_for_config(cfg):
                seen["api_key"] = cfg.api_key
                return object()

        ev._resolve(_Analyzer())
        return seen.get("api_key")

    @pytest.mark.parametrize("provider,env_var", sorted(
        (p.value, e) for p, e in _PROVIDER_KEY_ENV.items()))
    def test_shadow_eval_finds_the_key_where_it_lives(
            self, provider, env_var, monkeypatch):
        got = self._resolved_key(monkeypatch, provider,
                                 {env_var: "shadow-" + provider})
        assert got == "shadow-" + provider, (
            f"shadow eval looked somewhere other than {env_var}")

    def test_the_explicit_override_still_wins(self, monkeypatch):
        """Operators set LLM_SHADOW_API_KEY deliberately; do not break that."""
        got = self._resolved_key(monkeypatch, "grok", {
            "LLM_SHADOW_API_KEY": "override",
            "XAI_API_KEY": "provider-specific"})
        assert got == "override"

    def test_the_underscore_llm_spelling_still_works(self, monkeypatch):
        """The middle lookup predates this change; it is not ours to remove."""
        got = self._resolved_key(monkeypatch, "groq",
                                 {"GROQ_LLM_API_KEY": "middle"})
        assert got == "middle"


class TestEveryHintNamesSomethingThatWorks:
    """Direction 1: what the card sends to /setllm, /setllm can set."""

    @pytest.mark.parametrize("key", sorted(_DEFAULT_MANAGED))
    def test_a_setllm_hint_means_a_provider_maps_to_that_key(self, key):
        if vault_fix_hint(key) != SETLLM:
            return
        assert key in settable_key_envs(), (
            f"/vault tells the operator to run `{SETLLM}` for {key}, and no "
            "provider writes it — the command cannot produce the outcome")

    @pytest.mark.parametrize("key", [
        "ONCHAIN_API_KEY", "HYPERLIQUID_API_KEY", "LLM_API_KEY",
    ])
    def test_the_four_that_took_the_wrong_route(self, key):
        assert vault_fix_hint(key) != SETLLM

    def test_xai_now_takes_the_route_and_the_route_works(self):
        """It was already routed to /setllm; the command is what was broken."""
        assert vault_fix_hint("XAI_API_KEY") == SETLLM
        assert "XAI_API_KEY" in settable_key_envs()


class TestNothingSettableIsSentToEnv:
    """Direction 2: what /setllm can set, the card does not send to a file."""

    @pytest.mark.parametrize("env_var", sorted(_PROVIDER_KEY_ENV.values()))
    def test_a_settable_managed_key_is_offered_the_command(self, env_var):
        if env_var not in _DEFAULT_MANAGED:
            return
        assert vault_fix_hint(env_var) == SETLLM, (
            f"{env_var} can be set by /setllm and the card says "
            f"`{vault_fix_hint(env_var)}` — an operator sent to edit a file "
            "leaves the plaintext copy behind")


class TestOneCredentialGetsOneInstruction:
    def test_all_four_hyperliquid_keys_agree(self):
        """API_KEY said `/setllm` and API_SECRET said `.env`, on one card."""
        hints = {vault_fix_hint(k) for k in _DEFAULT_MANAGED
                 if k.startswith("HYPERLIQUID")}
        assert len(hints) == 1, hints

    def test_the_hint_names_both_halves_the_venue_reads(self):
        """`has_operator_credentials` needs the pair; the hint has to say so."""
        hint = vault_fix_hint("HYPERLIQUID_PRIVATE_KEY")
        assert "HYPERLIQUID_WALLET_ADDRESS" in hint
        assert "HYPERLIQUID_PRIVATE_KEY" in hint


class TestTheKeyThatSignsIsProtected:
    def test_the_private_key_is_vault_managed(self):
        """venues.py: live Hyperliquid needs WALLET_ADDRESS + PRIVATE_KEY.

        The vault held the address and two names read nowhere in the tree, and
        left out the agent-wallet private key that signs the orders.
        """
        assert "HYPERLIQUID_PRIVATE_KEY" in _DEFAULT_MANAGED

    def test_the_venue_goes_dark_without_the_key_this_protects(self):
        """Drive the reader, not an annotation. This is WHY it must be vaulted.

        A first draft of this asserted a field name on a config class I had
        invented (`TradingConfig`; it is `ExchangeConfig`) — the "grep a name
        you remember" failure, in the guard written for a missing key. The
        venue's own gate is the thing to ask.
        """
        from types import SimpleNamespace

        from bot.core.venues import HyperliquidVenue
        v = HyperliquidVenue()
        both = SimpleNamespace(hyperliquid_wallet_address="0xabc",
                               hyperliquid_private_key="0x" + "1" * 64)
        assert v.has_operator_credentials(both) is True
        # A wiped .env with only the address restored from the vault: this is
        # the state the missing entry produced, and it stops the venue dead.
        addr_only = SimpleNamespace(hyperliquid_wallet_address="0xabc",
                                    hyperliquid_private_key="")
        assert v.has_operator_credentials(addr_only) is False


class TestTheCardItselfSaysIt:
    """A fix in the reading and not the renderer has not landed.

    Everything above drives `optional_venue_absences` directly. `_cmd_vault`
    is what an operator reads, and the wiring between them — which keys count
    as "already configured" — lives in the method, not in the seam. This
    repo's own record has that exact gap shipping twice.
    """

    @pytest.fixture()
    def card(self, monkeypatch, tmp_path):
        import bot.core.secrets_vault as sv
        import bot.utils.creds_sealing as cs
        from bot.skills.telegram_handler import TelegramHandler

        monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
        cs._cache.clear()

        async def _render(status):
            monkeypatch.setattr(sv, "vault_status", lambda: status)
            sent: list[str] = []
            h = TelegramHandler.__new__(TelegramHandler)

            async def _send(update, text, *a, **k):
                sent.append(str(text))

            h._send = _send
            h._is_admin = lambda update: True
            await h._cmd_vault(SimpleNamespace(effective_user=SimpleNamespace(id=1)),
                               SimpleNamespace(args=[]))
            return sent[-1]

        yield _render
        cs._cache.clear()

    @staticmethod
    def _hl(private_key_present: bool):
        """A box using Hyperliquid, with and without its signing key vaulted."""
        st = {
            "TELEGRAM_BOT_TOKEN": {"env": True, "vault": True, "state": "readable"},
            "HYPERLIQUID_WALLET_ADDRESS": {"env": True, "vault": True,
                                           "state": "readable"},
            "HYPERLIQUID_PRIVATE_KEY": (
                {"env": True, "vault": True, "state": "readable"}
                if private_key_present
                else {"env": False, "vault": False, "state": "absent"}),
            "HYPERLIQUID_API_KEY": {"env": False, "vault": False, "state": "absent"},
        }
        return st

    @pytest.mark.asyncio
    async def test_the_missing_signing_key_is_named_on_the_card(self, card):
        """The silent stop, as the operator would see it."""
        out = await card(self._hl(private_key_present=False))
        assert "Missing" in out
        assert "HYPERLIQUID_PRIVATE_KEY" in out, (
            "the venue cannot trade and the card said nothing was missing")

    @pytest.mark.asyncio
    async def test_a_healthy_venue_raises_nothing(self, card):
        """A card that cries wolf is a card operators learn to skim.

        `boot_health.py` records that lesson about WEB_CREDS_KEY, and it is
        why the suppression exists at all.
        """
        out = await card(self._hl(private_key_present=True))
        missing_section = out.split("Missing")[1] if "Missing" in out else ""
        assert "HYPERLIQUID_PRIVATE_KEY" not in missing_section

    @pytest.mark.asyncio
    async def test_an_unused_venue_is_not_reported_missing(self, card):
        """All four absent: this operator does not trade Hyperliquid."""
        st = {
            "TELEGRAM_BOT_TOKEN": {"env": True, "vault": True, "state": "readable"},
            "HYPERLIQUID_WALLET_ADDRESS": {"env": False, "vault": False,
                                           "state": "absent"},
            "HYPERLIQUID_PRIVATE_KEY": {"env": False, "vault": False,
                                        "state": "absent"},
        }
        out = await card(st)
        assert "HYPERLIQUID" not in out


class TestHalfAVenueIsNotAnUnusedVenue:
    """Absent alone is not a measurement; absent beside a sibling is."""

    def test_an_unused_venue_stays_quiet(self):
        assert optional_venue_absences(
            ["HYPERLIQUID_WALLET_ADDRESS", "HYPERLIQUID_PRIVATE_KEY"], set()) == []

    def test_a_stored_address_with_no_key_is_reported(self):
        """The silent stop: .env wiped, address restored, key never vaulted.

        `has_operator_credentials` goes False and Hyperliquid stops trading,
        with a card that showed nothing missing.
        """
        assert optional_venue_absences(
            ["HYPERLIQUID_PRIVATE_KEY"], {"HYPERLIQUID_WALLET_ADDRESS"}
        ) == ["HYPERLIQUID_PRIVATE_KEY"]

    def test_the_dead_names_are_never_reported(self):
        """API_KEY/_API_SECRET are read nowhere; reporting them is noise."""
        assert optional_venue_absences(
            ["HYPERLIQUID_API_KEY", "HYPERLIQUID_API_SECRET"],
            {"HYPERLIQUID_WALLET_ADDRESS", "HYPERLIQUID_PRIVATE_KEY"}) == []

    def test_it_generalises_to_the_other_optional_venues(self):
        assert optional_venue_absences(["BYBIT_API_SECRET"], set()) == []
        assert optional_venue_absences(
            ["BYBIT_API_SECRET"], {"BYBIT_API_KEY"}) == ["BYBIT_API_SECRET"]

    def test_a_key_from_no_optional_venue_is_never_added(self):
        """This function only ever ADDS to the card; it must not add the world."""
        assert optional_venue_absences(
            ["ANTHROPIC_API_KEY", "WEB3_SIGNER_PRIVATE_KEY"],
            {"BITGET_API_KEY"}) == []
