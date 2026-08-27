"""The self-hosted model went away and nothing said so.

The in-house model is served from a machine the operator controls, reached over
a tunnel. On 2026-08-19 the configured URL had been dead long enough that the
tunnel it named no longer existed — with all three routed tiers (chat, scan,
thesis) pointing at it — and the logs showed nothing at all.

That silence is structural, not bad luck. The LLM fallback chain catches every
failed call and answers from another provider, so there is no error to find.
The symptom is the in-house model quietly never being used again, and slightly
slower analysis. Nobody notices for as long as nobody looks.

THE GATEWAY PROBE ALREADY SOLVED THIS ONE SERVICE OVER, and its docstring
describes this exact failure: "an ephemeral tunnel URL rotating on restart
breaks it silently and looks exactly like a firewall." So this is that probe,
pointed at the model origin, with the same interval, the same two-strike rule
and the same recovery notice.

PROBED WITH THE CREDENTIAL THE BOT WOULD SEND. An unauthenticated check returns
the same 401 for a healthy endpoint and for one behind an access policy the bot
can never pass — it cannot fail, so it proves nothing. That is the whole lesson
of `test_gateway_health_route_exists.py`, and it cost two rounds of manual
debugging again on the day this was written before anyone sent the key.

FOUR OUTCOMES, FOUR REMEDIES. `unreachable` is a dead URL, `forbidden` is a
key or an access policy, `model_missing` is an endpoint that is healthy in
every way except that it does not serve the model the config names — which
404s every call while looking perfectly fine — and `ok` is ok.
"""

from __future__ import annotations

import pathlib
import re


from bot.core.proactive_monitor import ProactiveMonitor, _host_of

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _mon():
    m = ProactiveMonitor.__new__(ProactiveMonitor)
    m._llm_probe = None
    m._llm_probe_at = 0.0
    m._llm_alerted_state = ""
    return m


def _probe(m, state, **kw):
    m._llm_probe = {"state": state, "tier": "chat", "host": "h.trycloudflare.com",
                    "consecutive_failures": kw.pop("fails", 2), **kw}
    return m._check_llm_endpoint()


# ── it pages, once, for the right reason ────────────────────────────────────

def test_a_dead_endpoint_pages():
    alerts = _probe(_mon(), "unreachable")
    assert len(alerts) == 1
    body = alerts[0].body
    assert "UNREACHABLE" in body
    assert "h.trycloudflare.com" in body, "the alert does not name the host"


def test_one_failure_is_not_an_outage():
    """Matches the gateway's two-strike rule: one is a blip."""
    assert _probe(_mon(), "unreachable", fails=1) == []


def test_the_same_fault_does_not_page_twice():
    m = _mon()
    assert _probe(m, "unreachable")
    assert _probe(m, "unreachable", fails=3) == [], (
        "an unchanged outage re-paged — the noise problem this repo keeps fixing")


def test_a_changed_fault_does_page_again():
    """unreachable -> forbidden is a different problem with a different fix,
    and must not be swallowed as 'already told them'."""
    m = _mon()
    assert _probe(m, "unreachable")
    assert _probe(m, "forbidden", status=401), (
        "the fault changed and the operator was not told")


def test_recovery_is_announced():
    m = _mon()
    _probe(m, "unreachable")
    alerts = _probe(m, "ok", fails=0)
    assert alerts and "BACK" in alerts[0].body


def test_a_healthy_endpoint_that_was_never_down_says_nothing():
    assert _probe(_mon(), "ok", fails=0) == []


def test_no_probe_result_means_no_claim():
    """Not configured for a self-hosted provider → no probe, and therefore
    nothing asserted either way. Absent is not a failure."""
    m = _mon()
    m._llm_probe = None
    assert m._check_llm_endpoint() == []


# ── the four states read differently ────────────────────────────────────────

def test_each_fault_explains_its_own_remedy():
    unreachable = _probe(_mon(), "unreachable")[0].body
    forbidden = _probe(_mon(), "forbidden", status=401)[0].body
    missing = _probe(_mon(), "model_missing", model="v10-8b",
                     served=["v8-8b"])[0].body

    assert "quick tunnel" in unreachable.lower()
    assert "access policy" in forbidden.lower(), (
        "a 401 must name the access-policy case — no API key ever passes one, "
        "and it is indistinguishable from a wrong key without being told")
    assert "v10-8b" in missing and "v8-8b" in missing
    assert "404" in missing, (
        "a healthy endpoint serving the wrong model is the failure that looks "
        "most like success; say what actually happens")
    assert len({unreachable, forbidden, missing}) == 3, (
        "two faults produced the same text, so the alert cannot tell them apart")


def test_the_alert_says_trading_is_unaffected():
    """A red alert about the model must not read as a trading incident. The
    fallback chain is answering; the operator needs to know the difference."""
    body = _probe(_mon(), "unreachable")[0].body
    assert "UNAFFECTED" in body


def test_the_host_is_named_but_never_the_key_or_the_path():
    """A URL is the field most likely to carry a credential — a signed link, a
    token in a query. /readyz answers with a coarse reason for this reason."""
    assert _host_of("https://x.trycloudflare.com/v1?token=SECRET") == "x.trycloudflare.com"
    assert _host_of("http://localhost:11434/v1") == "localhost"
    assert "SECRET" not in _host_of("https://a.b/v1?k=SECRET")
    assert _host_of("") and _host_of("not a url")     # never raises, never blank


# ── it only probes what it should ───────────────────────────────────────────

def test_only_a_self_hosted_tier_is_probed(monkeypatch):
    """An operator on hosted APIs gets no probe and no noise. A hosted provider
    going down is Anthropic's problem, not a rotated tunnel."""
    m = _mon()
    for t in ("SCAN", "THESIS", "LEARNING", "CHAT"):
        monkeypatch.delenv(f"LLM_TIER_{t}_PROVIDER", raising=False)
    assert m._llm_origin() == ("", "", "")

    monkeypatch.setenv("LLM_TIER_CHAT_PROVIDER", "gemini")
    assert m._llm_origin() == ("", "", "")


def test_a_runeclaw_tier_yields_its_url_and_key(monkeypatch):
    m = _mon()
    for t in ("SCAN", "THESIS", "LEARNING", "CHAT"):
        monkeypatch.delenv(f"LLM_TIER_{t}_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_TIER_CHAT_PROVIDER", "runeclaw")
    monkeypatch.setenv("RUNECLAW_LLM_BASE_URL", "https://x.trycloudflare.com/v1")
    monkeypatch.setenv("RUNECLAW_LLM_API_KEY", "k" * 32)
    url, key, tier = m._llm_origin()
    assert url.endswith("/v1") and key == "k" * 32 and tier == "chat"


def test_ollama_counts_as_self_hosted_too(monkeypatch):
    m = _mon()
    for t in ("SCAN", "THESIS", "LEARNING", "CHAT"):
        monkeypatch.delenv(f"LLM_TIER_{t}_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_TIER_SCAN_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    assert m._llm_origin()[0] == "http://localhost:11434/v1"


# ── the wiring ──────────────────────────────────────────────────────────────

def test_the_probe_and_the_check_are_both_reached():
    """A correct probe nothing calls is this repository's signature failure."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "proactive_monitor.py")
                    .read_text(encoding="utf-8"))
    assert "await self._probe_llm_endpoint()" in src, (
        "the probe is never run, so it can never report anything")
    assert "alerts.extend(self._check_llm_endpoint())" in src, (
        "the check is never collected, so the probe's result never pages")


def test_the_probe_sends_the_credential():
    """An unauthenticated probe returns the same 401 for a healthy endpoint and
    one behind an access policy — it cannot fail, so it proves nothing."""
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "proactive_monitor.py")
                    .read_text(encoding="utf-8"))
    body = src[src.index("async def _probe_llm_endpoint"):src.index("def _check_llm_endpoint")]
    assert "Authorization" in body and "Bearer" in body, (
        "the probe no longer sends the key, so it verifies nothing past the edge")


# ── the bug this file caught while being written ────────────────────────────

def test_no_alert_body_repeats_its_own_header():
    """`"...\\n" "\\u2500" * 16` CONCATENATES BEFORE IT MULTIPLIES.

    Adjacent string literals are joined at compile time, so the `* 16` applies
    to the whole joined string and the line above the separator is repeated
    sixteen times. The severe anomaly card shipped exactly this, in this file,
    and the guard written for it was scoped to that one card — so writing a NEW
    card here reproduced it immediately.

    Scoped to the module now, not to a card: every separator must be a name.
    """
    from tests.source_scan import code_only

    src = code_only((ROOT / "bot" / "core" / "proactive_monitor.py")
                    .read_text(encoding="utf-8"))
    bad = re.findall(r'"[^"\n]*\\n"\s*\n?\s*"\\u2500"\s*\*\s*\d+', src)
    assert not bad, (
        f"a separator is multiplied against an adjacent literal: {bad[:2]} — "
        "that repeats the preceding line N times. Assign `sep = \"\\u2500\" * 16` "
        "and interpolate it")


def test_every_alert_this_module_builds_renders_its_header_once():
    """The property, driven rather than matched: each state's card must contain
    its own heading exactly once."""
    for state, extra in (("unreachable", {}), ("forbidden", {"status": 401}),
                         ("model_missing", {"model": "m", "served": ["x"]})):
        body = _probe(_mon(), state, **extra)[0].body
        assert body.count("IN-HOUSE MODEL UNREACHABLE") == 1, (
            f"{state}: header rendered {body.count('IN-HOUSE MODEL UNREACHABLE')} times")
        assert body.count("\u2500" * 16) == 2, (
            f"{state}: expected exactly two separator rules")
