"""The LLM-offline card made three claims and could support none of them.

Live, 2026-09-14, the operator was sent this while the bot kept trading:

    🚨 LLM BRAIN OFFLINE — RUNNING ON RULES
    Every LLM provider has failed for 9 analyses in a row (~1 min).
    Last error: Error code: 530 - {'type': '…/error-1033/', 'title': 'Error 103
    👉 Add or rotate an LLM API key (paid tier avoids the daily quota wall).

1. **"Every LLM provider has failed" was not measured.** `_try_llm_fallback`
   `continue`s past a provider with no key and past one whose client will not
   build. Neither is contacted, and both were counted as failures. A sixth
   outcome is quieter: a provider that ANSWERED with an unparseable reply also
   `continue`s — a working host, reported as a dead one.
2. **"Last error" was the FIRST error** — `str(exc)` from the primary path's
   `except`. Every fallback's error went to the audit log and nowhere else.
3. **The action line was unconditional**, and Cloudflare 1033 is a tunnel that
   is not connected: no key changes it. The branch for an EMPTY error was the
   more confident of the two, asserting the quota wall from nothing at all.

Driven, not scanned. The walk is produced inside a 170-line method in the
middle of a fallback chain, so the outcomes are planted and the card is read.
"""
import asyncio
import pathlib
from types import SimpleNamespace

import pytest

import bot.core.proactive_monitor as pm
from bot.core.analyzer import Analyzer
from bot.core.proactive_monitor import ProactiveMonitor
from bot.llm import failure_cause as fc
from bot.llm.provider import LLMProvider, fallback_chain
from bot.skills.scan_hints import _scan_timeout_hint

#: The error the live incident actually carried, verbatim as far as the card
#: truncated it. Every classification test anchors to THIS rather than to a
#: tidy "530" so that a widening of the network vocabulary cannot pass while
#: the real payload still misses.
LIVE_530 = (
    "Error code: 530 - {'type': 'https://developers.cloudflare.com/support/"
    "troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1033/', "
    "'title': 'Error 103")


# ── the cause reading ────────────────────────────────────────────────
class TestCauseIsRead:
    def test_the_live_outage_reads_as_a_network_fault(self):
        assert fc.cause_of(LIVE_530) == fc.NETWORK

    def test_and_its_action_does_not_ask_for_a_key(self):
        """The whole cost of the old card. An operator who acted on it would
        have bought a paid tier for an unreachable host."""
        action = fc.cause_action(LIVE_530)
        assert "rotate" not in action.lower()
        assert "paid tier" not in action.lower()
        assert "key" in action.lower()          # it says the key is NOT it
        assert "nothing about the key is implicated" in action.lower()

    @pytest.mark.parametrize("err,cause", [
        ("Error code: 401 - invalid x-api-key", fc.AUTH),
        ("authentication_error: bad key", fc.AUTH),
        ("Error code: 429 - RESOURCE_EXHAUSTED", fc.QUOTA),
        ("rate limit reached for gpt-4", fc.QUOTA),
        ("insufficient_quota: your credit balance is too low", fc.QUOTA),
        ("HTTPSConnectionPool: Read timed out", fc.NETWORK),
        ("getaddrinfo failed", fc.NETWORK),
        ("502 Bad Gateway", fc.NETWORK),
        ("model claude-3-sonnet has been decommissioned", fc.MODEL),
        ("Error code: 404 - model_not_found", fc.MODEL),
        ("something nobody has seen before", fc.UNKNOWN),
    ])
    def test_the_vocabulary(self, err, cause):
        assert fc.cause_of(err) == cause

    def test_an_absent_error_is_unrecorded_and_not_a_cause(self):
        """The old card's `else` branch asserted "free-tier API quota
        exhausted … across every provider" from an empty string: the emptiest
        evidence producing the most specific diagnosis. UNRECORDED is a
        different value from UNKNOWN because they send an operator to
        different places — one has an error to read, the other does not."""
        for empty in ("", "   ", None):
            assert fc.cause_of(empty) == fc.UNRECORDED
        assert fc.cause_of("something") == fc.UNKNOWN
        assert "quota" not in fc.cause_action("").lower()
        assert "429" not in fc.cause_line("")

    def test_every_cause_has_text_and_none_of_them_is_bare(self):
        for cause in (fc.AUTH, fc.QUOTA, fc.NETWORK, fc.MODEL,
                      fc.UNKNOWN, fc.UNRECORDED):
            icon, reading, action = fc.CAUSE_TEXT[cause]
            assert icon and reading and action

    def test_only_the_two_key_causes_mention_rotating_one(self):
        """A card that names an action is claiming the action helps. Four of
        the six causes are not helped by touching a key at all."""
        for cause in (fc.NETWORK, fc.MODEL, fc.UNKNOWN, fc.UNRECORDED):
            action = fc.CAUSE_TEXT[cause][2].lower()
            assert "rotate the key" not in action

    def test_the_auth_branch_is_the_one_that_condemns_keys_live(self):
        """Delegated to `key_health.looks_like_auth_error`, deliberately: that
        function decides, in `_analyze_with_llm`, whether to mark the key
        invalid. A second opinion here would let the card say "the host was
        unreachable" over an auto-healer that had just condemned the key."""
        from bot.llm import key_health
        for err in ("Error code: 401 - invalid x-api-key",
                    "authentication_error", "invalid api key"):
            assert key_health.looks_like_auth_error(err)
            assert fc.cause_of(err) == fc.AUTH


# ── what the chain actually did ──────────────────────────────────────
def _walk(*pairs):
    return [{"provider": p, "outcome": o, "error": ""} for p, o in pairs]


class TestAttemptSummary:
    def test_an_absent_walk_is_not_an_empty_one(self):
        """`None` means nothing was recorded; `[]` means the chain had no step
        to take. Reading the first as the second would let the card announce a
        measured zero about a measurement that never happened."""
        assert fc.attempt_summary(None)["readable"] is False
        assert fc.attempt_summary("nonsense")["readable"] is False
        empty = fc.attempt_summary([])
        assert empty["readable"] is True and empty["total"] == 0

    def test_never_contacted_is_counted_apart_from_failed(self):
        s = fc.attempt_summary(_walk(
            ("alibaba", fc.SKIPPED_PRIMARY), ("gemini", fc.NO_KEY),
            ("groq", fc.NO_CLIENT), ("deepseek", fc.FAILED)))
        assert s["contacted"] == 1          # deepseek only
        assert s["not_contacted"] == 2      # gemini, groq
        assert s["skipped_primary"] == 1
        assert s["total"] == 4

    def test_an_unparseable_reply_is_a_contact(self):
        """The sixth outcome. A provider that ANSWERED is a working host, and
        the card must not report it as one that could not be reached."""
        s = fc.attempt_summary(_walk(("groq", fc.UNPARSEABLE)))
        assert s["contacted"] == 1 and s["not_contacted"] == 0

    def test_the_two_buckets_cannot_overlap(self):
        """`attempt_summary` checks _CONTACTED first, so an outcome added to
        BOTH sets is silently absorbed — a mutation that put UNPARSEABLE in
        _NOT_CONTACTED while leaving it in _CONTACTED survived the round with
        every test green, because it changed nothing. The invariant the
        elif-chain relies on is that the buckets are disjoint; asserting it
        here is what makes a half-move visible."""
        assert not (fc._CONTACTED & fc._NOT_CONTACTED)
        assert fc.SKIPPED_PRIMARY not in fc._CONTACTED
        assert fc.SKIPPED_PRIMARY not in fc._NOT_CONTACTED

    def test_a_step_it_cannot_read_is_counted_as_unclassified(self):
        s = fc.attempt_summary([{"provider": "x", "outcome": "future_thing"},
                                "not a dict"])
        assert s["unclassified"] == 2
        assert s["contacted"] == 0 and s["not_contacted"] == 0


class TestCoverageSentence:
    def test_the_live_shape_does_not_claim_every_provider(self):
        """One key configured — the ordinary deployment. One provider tried,
        three never contacted."""
        out = fc.chain_coverage_sentence(_walk(
            ("alibaba", fc.SKIPPED_PRIMARY), ("gemini", fc.NO_KEY),
            ("groq", fc.NO_KEY), ("deepseek", fc.NO_KEY)), 9)
        assert "1 of 4" in out
        assert "never contacted" in out
        assert "Every LLM provider has failed" not in out

    def test_every_provider_tried_is_allowed_to_say_so(self):
        out = fc.chain_coverage_sentence(_walk(
            ("alibaba", fc.SKIPPED_PRIMARY), ("gemini", fc.FAILED)), 4)
        assert "Every provider that was tried (2) failed" in out
        assert "never contacted" not in out

    def test_nothing_contacted_at_all_gets_its_own_sentence(self):
        """The case the old wording was furthest from: nothing "failed",
        because nothing was asked."""
        out = fc.chain_coverage_sentence(_walk(
            ("gemini", fc.NO_KEY), ("groq", fc.NO_KEY)), 6)
        assert "No LLM provider could be contacted at all" in out
        assert "failed" not in out.replace("fell back", "")

    def test_an_unrecorded_walk_claims_no_count(self):
        out = fc.chain_coverage_sentence(None, 9)
        assert "not recorded" in out
        assert "<b>9</b>" in out            # the streak IS measured
        assert " of " not in out            # the coverage is not

    def test_the_duration_lands_beside_the_streak(self):
        out = fc.chain_coverage_sentence(None, 9, " (~9 min)")
        assert "<b>9</b> analyses in a row (~9 min)" in out

    def test_one_analysis_is_not_pluralised(self):
        assert "1</b> analysis in a row" in fc.chain_coverage_sentence(None, 1)


# ── the analyzer records it ──────────────────────────────────────────
def _bare():
    """The fixture the existing suite uses: an Analyzer with only the health
    state, no heavy __init__."""
    a = Analyzer.__new__(Analyzer)
    a._llm_degraded_streak = 0
    a._llm_last_ok_monotonic = 0.0
    a._llm_degraded_since_monotonic = 0.0
    a._llm_last_error = ""
    return a


class TestAnalyzerRecordsTheWalk:
    def test_a_bare_analyzer_still_reports_and_says_it_recorded_nothing(self):
        """`llm_health` runs on analyzers built without __init__, and a
        snapshot that RAISED here would take the whole card down."""
        assert _bare().llm_health()["chain_walk"] is None

    def test_the_primary_failure_is_written_into_the_walk(self):
        """`_try_llm_fallback` records the primary as SKIPPED_PRIMARY with no
        error — the exception lives one frame up. This is the only place that
        holds both."""
        a = _bare()
        a._llm_last_chain_walk = [
            {"provider": "alibaba", "outcome": fc.SKIPPED_PRIMARY, "error": ""},
            {"provider": "gemini", "outcome": fc.NO_KEY, "error": ""}]
        a._note_llm_degraded(LIVE_530, primary="alibaba")
        walk = a.llm_health()["chain_walk"]
        assert walk[0]["provider"] == "alibaba"
        assert walk[0]["error"] == LIVE_530[:200]

    def test_a_primary_outside_the_chain_is_added_rather_than_lost(self):
        """Tier routing can point the primary at a provider the analysis chain
        does not list, in which case the walk has no row for the provider whose
        failure started everything."""
        a = _bare()
        a._llm_last_chain_walk = [
            {"provider": "gemini", "outcome": fc.NO_KEY, "error": ""}]
        a._note_llm_degraded("boom", primary="openai")
        walk = a.llm_health()["chain_walk"]
        assert [s["provider"] for s in walk] == ["openai", "gemini"]
        assert walk[0]["outcome"] == fc.SKIPPED_PRIMARY

    def test_a_success_clears_the_walk_with_the_streak(self):
        """The walk is evidence FOR a streak. Keeping it past the recovery
        would let a later reader describe today's silence with yesterday's
        chain."""
        a = _bare()
        a._llm_last_chain_walk = [
            {"provider": "gemini", "outcome": fc.FAILED, "error": "x"}]
        a._note_llm_degraded("x", primary="gemini")
        assert a.llm_health()["chain_walk"]
        a._note_llm_ok()
        assert a.llm_health()["chain_walk"] is None

    def test_the_walk_is_a_copy_not_the_live_list(self):
        """The handoff attribute is rewritten by the next fallback call. A
        snapshot that aliased it would mutate under the card."""
        a = _bare()
        a._llm_last_chain_walk = [
            {"provider": "gemini", "outcome": fc.FAILED, "error": "first"}]
        a._note_llm_degraded("first", primary="gemini")
        held = a.llm_health()["chain_walk"]
        a._llm_last_chain_walk.clear()
        a._llm_last_chain_walk.append(
            {"provider": "groq", "outcome": fc.FAILED, "error": "second"})
        assert held == a.llm_health()["chain_walk"]
        assert held[0]["error"] == "first"

    def test_recording_never_turns_one_failure_into_two(self):
        """Instrumentation inside a fallback path. A walk that is not a list —
        an attribute somebody set to a sentinel, a partially built analyzer —
        answers None rather than raising through the degrade note."""
        a = _bare()
        a._llm_last_chain_walk = "not a list"
        a._note_llm_degraded("x", primary="gemini")
        assert a.llm_health()["chain_walk"] is None
        assert a.llm_health()["degraded_streak"] == 1


# ── the card reads it ────────────────────────────────────────────────
def _card(monkeypatch, walk, last_error=LIVE_530, streak=9):
    monkeypatch.setattr(pm, "CONFIG", SimpleNamespace(analyzer=SimpleNamespace(
        llm_degraded_alert_enabled=True, llm_degraded_alert_min_streak=3)))
    health = {"degraded_streak": streak, "degraded_seconds": streak * 60.0,
              "last_ok_seconds_ago": None, "last_error": last_error,
              "chain_walk": walk}
    mon = ProactiveMonitor(SimpleNamespace(
        analyzer=SimpleNamespace(llm_health=lambda: health)))
    out = mon._check_llm_degraded()
    assert len(out) == 1
    return out[0].body


LIVE_WALK = [{"provider": "alibaba", "outcome": fc.SKIPPED_PRIMARY,
              "error": LIVE_530}] + [
    {"provider": p, "outcome": fc.NO_KEY, "error": ""}
    for p in ("gemini", "groq", "deepseek")]


class TestTheCard:
    def test_the_live_card_makes_none_of_the_three_claims(self, monkeypatch):
        body = _card(monkeypatch, LIVE_WALK)
        # 1. not "every provider"
        assert "Every LLM provider has failed" not in body
        assert "1 of 4 providers was tried" in body
        assert "never contacted" in body
        # 2. not "Last error"
        assert "Last error" not in body
        assert "Primary provider's error" in body
        # 3. not a key rotation for a network fault
        assert "rotate an LLM API key" not in body
        assert "daily quota wall" not in body
        assert "tunnel is up" in body

    def test_an_empty_error_no_longer_asserts_the_quota_wall(self, monkeypatch):
        body = _card(monkeypatch, None, last_error="")
        assert "RESOURCE_EXHAUSTED" not in body
        assert "quota" not in body.lower()
        assert "no error was recorded" in body
        assert "not recorded" in body       # and the coverage abstains too

    def test_the_raw_error_is_escaped(self, monkeypatch):
        body = _card(monkeypatch, LIVE_WALK,
                     last_error="<script>alert(1)</script>")
        assert "<script>" not in body
        assert "&lt;script&gt;" in body

    def test_an_auth_failure_gets_the_key_advice_it_always_should_have(
            self, monkeypatch):
        body = _card(monkeypatch, LIVE_WALK,
                     last_error="Error code: 401 - invalid x-api-key")
        assert "/setllm" in body
        assert "tunnel" not in body

    def test_a_quota_failure_names_the_paid_tier(self, monkeypatch):
        body = _card(monkeypatch, LIVE_WALK,
                     last_error="Error code: 429 - RESOURCE_EXHAUSTED")
        assert "paid tier" in body
        assert "/setllm" not in body

    def test_the_card_still_fires_and_still_reaches_only_the_operator(
            self, monkeypatch):
        """`last_error` is a raw provider string and the two actions are the
        operator's. The audience decision predates this change and must
        survive it."""
        monkeypatch.setattr(pm, "CONFIG", SimpleNamespace(
            analyzer=SimpleNamespace(llm_degraded_alert_enabled=True,
                                     llm_degraded_alert_min_streak=3)))
        mon = ProactiveMonitor(SimpleNamespace(analyzer=SimpleNamespace(
            llm_health=lambda: {"degraded_streak": 3, "degraded_seconds": 1.0,
                                "last_ok_seconds_ago": None,
                                "last_error": LIVE_530,
                                "chain_walk": LIVE_WALK})))
        alert = mon._check_llm_degraded()[0]
        assert alert.alert_type == "LLM_DEGRADED"
        assert alert.severity == "CRITICAL"
        assert alert.audience == "admin"

    def test_a_health_snapshot_with_no_walk_key_still_renders(self, monkeypatch):
        """A snapshot from an older analyzer, or from a stand-in. Absent is
        read as unrecorded, not as an error and not as zero providers."""
        monkeypatch.setattr(pm, "CONFIG", SimpleNamespace(
            analyzer=SimpleNamespace(llm_degraded_alert_enabled=True,
                                     llm_degraded_alert_min_streak=3)))
        mon = ProactiveMonitor(SimpleNamespace(analyzer=SimpleNamespace(
            llm_health=lambda: {"degraded_streak": 5, "degraded_seconds": 0.0,
                                "last_ok_seconds_ago": None})))
        body = mon._check_llm_degraded()[0].body
        assert "not recorded" in body


# ── the commands the card names ──────────────────────────────────────
class TestTheDoorsItNames:
    def test_every_chain_provider_can_actually_be_set_with_setllm(self):
        """The AUTH action says "/setllm <provider> <key>". That is a claim
        that the command writes the key for the provider that just failed —
        and /setllm has been ten-of-eleven before, silently storing nothing
        for the missing one under a help text promising the key survives a
        redeploy.

        THE OBVIOUS ASSERTION HERE IS TAUTOLOGICAL and was written first:
        `settable_key_envs()` IS `frozenset(_PROVIDER_KEY_ENV.values())`, and
        `fallback_chain` drops any provider `_PROVIDER_KEY_ENV` does not name
        — so comparing the two can never fail, whatever anybody breaks. A test
        that passes for a reason unrelated to its rule is worth less than none.

        The real seam is that the two sides key the same map DIFFERENTLY.
        `fallback_chain` looks up the ENUM; `/setllm` calls
        `provider_key_env(provider_str)` on the lowercase STRING the operator
        types. This drives that round trip — the lookup the command actually
        performs, on the name the card actually tells them to type."""
        from bot.llm.provider import provider_key_env
        for kind in ("analysis", "chat"):
            for provider, key_env, _model in fallback_chain(kind,
                                                            is_admin=True):
                typed = provider_key_env(provider.value)
                assert typed == key_env, (
                    f"the card would tell an operator to run "
                    f"`/setllm {provider.value} <key>`, and that command "
                    f"resolves the name to {typed!r} while the {kind} chain "
                    f"reads the key from {key_env!r}")

    def test_the_two_commands_it_names_are_registered(self):
        """`/setllm` and `/llmtiers` are read out of the handler's own
        registration table rather than grepped for, so a rename moves both
        together or fails here."""
        import bot.skills.telegram_handler as th
        src = th.__file__
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        for cmd in ("setllm", "llmtiers"):
            assert f'("{cmd}", self._cmd_{cmd})' in text


# ── the walk is PRODUCED, not just consumed ──────────────────────────
#
# Every test above plants a walk by hand, and in the first mutation round that
# let "_try_llm_fallback stops recording the no-key step" SURVIVE with all 42
# green: the recording code was inert under the whole suite. It is the same
# trap PR #357's own round hit — a guard that builds the input by hand cannot
# see the producer that builds it in production. These drive the real method.


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = None


class _Completions:
    def __init__(self, answer):
        self._answer = answer

    async def create(self, **kwargs):
        if isinstance(self._answer, Exception):
            raise self._answer
        return _Resp(self._answer)


class _Chat:
    def __init__(self, answer):
        self.completions = _Completions(answer)


class _FakeClient:
    def __init__(self, answer):
        self.chat = _Chat(answer)


def _fallback_analyzer():
    """Enough of an Analyzer to run `_try_llm_fallback`. `_parse_llm_response`
    is stubbed rather than real: the subject here is which OUTCOME the walk
    records, and the parse verdict is the input to that, not the thing under
    test."""
    a = Analyzer.__new__(Analyzer)
    a._llm_calls_today = 0
    a._cost = None
    a._llm_config = None
    a._llm_last_chain_walk = []
    a._parse_llm_response = lambda raw: (
        {"_parsed": True, "direction": "LONG", "confidence": 0.6}
        if raw.strip().startswith("{") else {"_parsed": False})
    a._estimate_tokens = lambda text: len(text) // 4
    return a


def _clear_chain_keys(monkeypatch):
    """Every analysis-chain key env removed, so the no-key branch is the one
    that runs. Read out of `fallback_chain` rather than listed here — a list
    would be a second copy of the chain."""
    for _p, key_env, _m in fallback_chain("analysis", is_admin=True):
        monkeypatch.delenv(key_env, raising=False)


class TestTheFallbackLoopRecordsWhatItDid:
    def test_a_chain_with_no_keys_records_every_step_as_never_contacted(
            self, monkeypatch):
        """The ordinary deployment's quiet half, and the one the old card
        called "Every LLM provider has failed"."""
        _clear_chain_keys(monkeypatch)
        a = _fallback_analyzer()
        out = asyncio.run(a._try_llm_fallback(
            "p", None, False, failed_provider="openai", is_admin=False))
        assert out is None
        walk = a._llm_last_chain_walk
        expected = [p.value for p, _k, _m in fallback_chain("analysis")]
        assert [s["provider"] for s in walk] == expected
        assert {s["outcome"] for s in walk} == {fc.NO_KEY}
        summary = fc.attempt_summary(walk)
        assert summary["contacted"] == 0
        assert summary["not_contacted"] == len(expected)

    def test_the_provider_that_already_failed_is_marked_as_such(
            self, monkeypatch):
        _clear_chain_keys(monkeypatch)
        a = _fallback_analyzer()
        asyncio.run(a._try_llm_fallback(
            "p", None, False, failed_provider=LLMProvider.GEMINI.value))
        rows = {s["provider"]: s["outcome"] for s in a._llm_last_chain_walk}
        assert rows["gemini"] == fc.SKIPPED_PRIMARY
        assert rows["groq"] == fc.NO_KEY

    def test_a_client_that_will_not_build_is_never_contacted(
            self, monkeypatch):
        _clear_chain_keys(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", "k" * 20)
        monkeypatch.setattr("bot.core.analyzer.create_llm_client",
                            lambda cfg: None)
        a = _fallback_analyzer()
        asyncio.run(a._try_llm_fallback(
            "p", None, False, failed_provider="openai"))
        rows = {s["provider"]: s["outcome"] for s in a._llm_last_chain_walk}
        assert rows["groq"] == fc.NO_CLIENT
        assert fc.attempt_summary(a._llm_last_chain_walk)["contacted"] == 0

    def test_a_provider_that_raises_records_its_own_error(self, monkeypatch):
        """The second false claim, at its source: this error used to reach the
        audit log and nowhere else, which is why the card's "Last error" was
        the primary's."""
        _clear_chain_keys(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", "k" * 20)
        monkeypatch.setattr(
            "bot.core.analyzer.create_llm_client",
            lambda cfg: _FakeClient(RuntimeError("groq said 503")))
        a = _fallback_analyzer()
        asyncio.run(a._try_llm_fallback(
            "p", SimpleNamespace(symbol="BTC/USDT"), False,
            failed_provider="openai"))
        row = [s for s in a._llm_last_chain_walk if s["provider"] == "groq"][0]
        assert row["outcome"] == fc.FAILED
        assert "503" in row["error"]
        assert fc.cause_of(row["error"]) == fc.NETWORK

    def test_a_provider_that_answered_unusably_is_not_a_dead_host(
            self, monkeypatch):
        """The sixth outcome. It ANSWERED — counting it as unreachable would
        send the operator to check a host that is up."""
        _clear_chain_keys(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", "k" * 20)
        monkeypatch.setattr("bot.core.analyzer.create_llm_client",
                            lambda cfg: _FakeClient("I am not JSON"))
        a = _fallback_analyzer()
        asyncio.run(a._try_llm_fallback(
            "p", SimpleNamespace(symbol="BTC/USDT"), False,
            failed_provider="openai"))
        row = [s for s in a._llm_last_chain_walk if s["provider"] == "groq"][0]
        assert row["outcome"] == fc.UNPARSEABLE
        assert fc.attempt_summary(a._llm_last_chain_walk)["contacted"] == 1

    def test_a_success_leaves_no_stale_walk_behind(self, monkeypatch):
        """A walk from a call that SUCCEEDED must not be readable as evidence
        for a later streak."""
        _clear_chain_keys(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", "k" * 20)
        monkeypatch.setattr(
            "bot.core.analyzer.create_llm_client",
            lambda cfg: _FakeClient('{"direction":"LONG","confidence":0.6}'))
        a = _fallback_analyzer()
        a._llm_last_chain_walk = [
            {"provider": "stale", "outcome": fc.FAILED, "error": "old"}]
        out = asyncio.run(a._try_llm_fallback(
            "p", SimpleNamespace(symbol="BTC/USDT"), False,
            failed_provider="openai"))
        assert out is not None
        assert "stale" not in [s["provider"] for s in a._llm_last_chain_walk]

    def test_the_walk_a_real_loop_produces_reaches_the_card(self, monkeypatch):
        """End to end with no hand-built walk anywhere: drive the loop, note
        the degrade from its result, render the card."""
        _clear_chain_keys(monkeypatch)
        a = _fallback_analyzer()
        a._llm_degraded_streak = 0
        a._llm_last_ok_monotonic = 0.0
        a._llm_degraded_since_monotonic = 0.0
        a._llm_last_error = ""
        asyncio.run(a._try_llm_fallback(
            "p", None, False, failed_provider="alibaba"))
        for _ in range(3):
            a._note_llm_degraded(LIVE_530, primary="alibaba")
        monkeypatch.setattr(pm, "CONFIG", SimpleNamespace(
            analyzer=SimpleNamespace(llm_degraded_alert_enabled=True,
                                     llm_degraded_alert_min_streak=3)))
        mon = ProactiveMonitor(SimpleNamespace(analyzer=a))
        body = mon._check_llm_degraded()[0].body
        n = len(fallback_chain("analysis"))
        assert f"1 of {n} providers was tried" in body
        assert "Every LLM provider has failed" not in body
        assert "rotate an LLM API key" not in body


# ── the two SIBLING surfaces that made the same claim ────────────────
#
# The corollary sweep, and the sharpest one this repo has had: the card's own
# footer says "/llmstatus — current provider + key", and /llmstatus carried a
# byte-for-byte version of both false claims —
#
#     🚨 Brain: DEGRADED — every provider has failed 9 analyses in a row;
#     running on the rule engine. Add/rotate an LLM key.
#
# So fixing the card alone would have sent the operator one tap sideways into
# an unfixed copy. `_scan_timeout_hint` made it a third time, and there the
# CAUSAL half is wrong in its own way: it blames the fallback chain for a
# timeout, which is right when providers are contacted and exactly backwards
# when they are skipped for want of a key.


def _health(walk, streak=9, last_error=LIVE_530):
    return {"degraded_streak": streak, "degraded_seconds": 540.0,
            "last_ok_seconds_ago": None, "last_error": last_error,
            "chain_walk": walk}


class TestScanTimeoutHint:
    def test_a_chain_nobody_contacted_is_not_why_the_scan_is_slow(self):
        """A keyless chain is an instant `continue` — the FASTEST path to the
        rule engine there is. Naming it as the likely cause of a timeout sends
        the operator to the one subsystem provably costing them no time, which
        is this function's own 37-tick lesson pointed the other way."""
        walk = [{"provider": p, "outcome": fc.NO_KEY, "error": ""}
                for p in ("alibaba", "gemini", "groq", "deepseek")]
        out = _scan_timeout_hint(SimpleNamespace(
            llm_health=lambda: _health(walk)))
        assert "NOT why this is slow" in out
        assert "Likely cause" not in out
        assert "burns through" not in out

    def test_a_contacted_chain_still_gets_the_timeout_explanation(self):
        walk = [{"provider": "alibaba", "outcome": fc.SKIPPED_PRIMARY,
                 "error": LIVE_530},
                {"provider": "gemini", "outcome": fc.FAILED, "error": "boom"}]
        out = _scan_timeout_hint(SimpleNamespace(
            llm_health=lambda: _health(walk)))
        assert "Likely cause" in out
        assert "pays a timeout" in out

    def test_it_no_longer_claims_every_provider(self):
        walk = [{"provider": "alibaba", "outcome": fc.SKIPPED_PRIMARY,
                 "error": LIVE_530},
                {"provider": "gemini", "outcome": fc.NO_KEY, "error": ""}]
        out = _scan_timeout_hint(SimpleNamespace(
            llm_health=lambda: _health(walk)))
        assert "every provider has failed" not in out
        assert "1 of 2" in out

    def test_an_unrecorded_walk_keeps_the_old_shape_without_the_old_claim(self):
        """No walk on record is not evidence that nothing was contacted. It
        falls to the timeout explanation — the historical behaviour — but says
        the coverage was not recorded rather than asserting it."""
        out = _scan_timeout_hint(SimpleNamespace(
            llm_health=lambda: _health(None)))
        assert "Likely cause" in out
        assert "not recorded" in out
        assert "every provider has failed" not in out


class TestLlmStatusLine:
    """`/llmstatus` builds its line inside a 90-line command handler that
    needs an Update, a context and a BYOK config, so the block is sliced out
    and run — the same treatment `panel_failure_honesty` gives app.js, and for
    the same reason: the claim lives in a branch no unit can otherwise reach.
    """

    @staticmethod
    def _block():
        import ast
        import textwrap
        src = pathlib.Path("bot/skills/llm_commands.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and "streak > 0" in ast.unparse(
                    node.test):
                body = "\n".join(ast.unparse(st) for st in node.body)
                return textwrap.dedent(body)
        raise AssertionError("the degraded branch of /llmstatus was not found")

    def _render(self, walk, last_error=LIVE_530, streak=9):
        import html as _html

        from bot.llm import failure_cause as _fc_
        env = {"h": _health(walk, streak, last_error), "html": _html,
               "_fc": _fc_, "streak": streak, "health_line": ""}
        exec(compile(self._block(), "<llmstatus>", "exec"), env)
        return env["health_line"]

    def test_the_command_the_card_names_makes_the_same_claim_no_longer(self):
        out = self._render(LIVE_WALK)
        assert "every provider has failed" not in out
        assert "Add/rotate an LLM key" not in out
        assert "1 of 4 providers was tried" in out
        assert "never contacted" in out

    def test_it_gives_the_action_that_matches_the_error(self):
        assert "tunnel is up" in self._render(LIVE_WALK)
        assert "/setllm" in self._render(
            LIVE_WALK, last_error="Error code: 401 - invalid x-api-key")
        assert "paid tier" in self._render(
            LIVE_WALK, last_error="Error code: 429 - RESOURCE_EXHAUSTED")

    def test_last_error_is_labelled_as_the_primary_s(self):
        out = self._render(LIVE_WALK)
        assert "Last error" not in out
        assert "Primary provider's error" in out

    def test_the_raw_error_is_still_escaped(self):
        out = self._render(LIVE_WALK, last_error="<b>x</b>")
        assert "<b>x</b>" not in out.replace("<b>Brain: DEGRADED</b>", "")
        assert "&lt;b&gt;x&lt;/b&gt;" in out

    def test_both_surfaces_answer_from_the_one_reading(self):
        """Not a byte-comparison of two sentences — that passes for a copy.
        Plant a walk and drive BOTH, and assert each carries the coverage the
        shared function produces for it."""
        walk = [{"provider": "alibaba", "outcome": fc.SKIPPED_PRIMARY,
                 "error": LIVE_530},
                {"provider": "gemini", "outcome": fc.FAILED, "error": "x"},
                {"provider": "groq", "outcome": fc.NO_KEY, "error": ""}]
        # NOT the same string: /llmstatus passes the elapsed duration into the
        # shared reading and the scan hint does not, so each is compared
        # against the reading AS IT CALLS IT. A byte-comparison of the two
        # sentences would have had to drop that difference, and a test that
        # drops a difference is a test that would pass over a copy.
        assert fc.chain_coverage_sentence(walk, 9, " (~9 min)") in self._render(walk)
        assert fc.chain_coverage_sentence(walk, 9) in _scan_timeout_hint(
            SimpleNamespace(llm_health=lambda: _health(walk)))
