"""Why did the brain go quiet, and how much of the chain actually answered?

`_check_llm_degraded` fires ONE card, and on 2026-09-14 that card made three
claims about a live outage and could support none of them:

    🚨 LLM BRAIN OFFLINE — RUNNING ON RULES
    Every LLM provider has failed for 9 analyses in a row (~1 min).
    Last error: Error code: 530 - {'type': '…/error-1033/', 'title': 'Error 103
    👉 Add or rotate an LLM API key (paid tier avoids the daily quota wall).

**"Every LLM provider has failed" is not a measurement.**
`Analyzer._try_llm_fallback` walks `FALLBACK_CHAINS["analysis"]` and `continue`s
past a provider with no key in the environment, and past one whose client will
not build. Neither was contacted, and from the streak's side they are
indistinguishable from a provider that was asked and raised. On a deployment
with one key configured — the ordinary deployment — exactly ONE provider was
tried and the card reports the other three as having failed. A sixth outcome
is quieter still: a provider that ANSWERED, whose reply did not parse, also
`continue`s. That is a working provider, counted as a dead one.

**"Last error" is the FIRST error.** The string handed to `_note_llm_degraded`
is `str(exc)` from the primary path's `except`. Every fallback provider's error
is swallowed by its own `except … continue` into the audit log and never
reaches the card. The label is wrong in the direction that matters: an operator
reading "Last error" believes they are looking at the end of the chain.

**And the action line is unconditional.** Cloudflare 1033 is a tunnel that is
not connected — the host was never reached, so no key on earth changes the
outcome, and "add or rotate an LLM API key" sends the operator to spend money
on the one thing that cannot help. Worse, the branch that runs when NO error
was recorded is the most specific of the two: it asserts "free-tier API quota
exhausted (429 / RESOURCE_EXHAUSTED) across every provider" from an empty
string. The emptiest evidence produced the most confident diagnosis.

Two vocabularies live in one file because one card reads both and a second copy
of either is a second answer. The cause reading DELEGATES its auth branch to
`key_health.looks_like_auth_error` rather than restating those four patterns —
that function is what condemns a key in the live auto-heal path, and a card
that disagreed with it about what an auth error looks like would be worse than
one that said nothing.
"""

from __future__ import annotations

from typing import Optional

from bot.llm import key_health

# ── Why the call failed ──────────────────────────────────────────────
#
# Never inferred from silence. `UNRECORDED` exists because "" is not a cause,
# and the branch this module replaces read it as the quota wall.

AUTH = "auth"                # the key is rejected
QUOTA = "quota"              # the key is fine and the allowance is spent
NETWORK = "network"          # the host was not reached at all
MODEL = "model"              # the model id is gone or not available to this key
UNKNOWN = "unknown"          # an error IS on record and matches nothing here
UNRECORDED = "unrecorded"    # no error string at all — nothing to read

#: (icon, how the error READS, what the operator can do about it).
#:
#: The middle field is deliberately hedged — "reads as", not "is". This is a
#: substring match over a driver's exception text, which is a heuristic, and a
#: heuristic is never a verdict. The third field is the load-bearing one: it is
#: what the old card got wrong, and only two of these six causes are helped by
#: touching a key at all.
CAUSE_TEXT: dict[str, tuple[str, str, str]] = {
    AUTH: ("🔑", "reads as a rejected key (401 / invalid api key)",
           "Rotate the key for this provider — /setllm &lt;provider&gt; &lt;key&gt;."),
    QUOTA: ("💳", "reads as a spent allowance (429 / quota / rate limit)",
            "Wait for the window to reset, or move this provider to a paid "
            "tier."),
    NETWORK: ("🌐", "reads as the host not being reached (5xx / tunnel / DNS / "
                    "timeout)",
              "Nothing about the key is implicated — check the provider's "
              "status and, for a tunnelled endpoint, that the tunnel is up."),
    MODEL: ("🧩", "reads as a model id that is gone or not available to this key",
            "Point this provider at a current model — /llmtiers shows what is "
            "configured."),
    UNKNOWN: ("❔", "matches none of the known signatures",
              "Read the error above — the audit log records each provider's "
              "own error separately."),
    # The one the old card was most confident about. An operator can still act
    # — but on evidence, not on a guess dressed as one. Neither of these two
    # names /llmstatus: the card's own footer already does, and an action line
    # that repeats the footer has told the reader nothing.
    UNRECORDED: ("❔", "no error was recorded, so the cause cannot be read here",
                 "The audit log records each provider's own error separately."),
}

#: Ordered most-specific-first. Order decides, so it is stated rather than left
#: to dict iteration: a body can legitimately carry more than one of these
#: tokens, and the two expensive mistakes are opposite — calling a network
#: fault `auth` sends the operator to rotate a good key while the outage runs,
#: and calling an auth fault `network` sends them to wait out a network that is
#: fine. `auth` is consulted first because its signatures are the narrowest
#: (`authentication_error`, `invalid x-api-key`) and because it is the one
#: branch already trusted, live, to condemn a key.
#:
#: THAT DELEGATION INHERITS A LOOSENESS AND THE INHERITANCE IS DELIBERATE.
#: `looks_like_auth_error` matches a bare `"401"` anywhere in the string, so an
#: error body carrying that substring for an unrelated reason — a request id, a
#: token count — reads as `auth` here even when a network signature is also
#: present. That same looseness decides, live, whether `_analyze_with_llm`
#: CONDEMNS the key in the key-health registry. Narrowing it here and not there
#: would give one error two answers: a card saying "the host was unreachable"
#: over an auto-healer that had just marked the key invalid. So this module
#: agrees with the condemning path by construction, and the looseness is filed
#: as its own question rather than forked into a second opinion.
_QUOTA_SIGNS = (
    "429", "resource_exhausted", "rate limit", "rate_limit", "ratelimit",
    "quota", "insufficient_quota", "billing", "credit balance",
    "too many requests",
)
_NETWORK_SIGNS = (
    "530", "1033", "cloudflare", "502", "503", "504", "521", "522", "523",
    "524", "bad gateway", "service unavailable", "gateway timeout",
    "connection", "connect", "timeout", "timed out", "dns", "getaddrinfo",
    "name or service not known", "ssl", "certificate", "tunnel",
    "unreachable", "reset by peer", "eof occurred",
)
_MODEL_SIGNS = (
    "model_not_found", "does not exist", "decommissioned", "deprecated",
    "no such model", "unknown model", "model not found",
)


def cause_of(err: Optional[str]) -> str:
    """One of AUTH / QUOTA / NETWORK / MODEL / UNKNOWN / UNRECORDED.

    An absent or blank error is UNRECORDED, never UNKNOWN and never a cause.
    The distinction is the whole point: UNKNOWN says "something was recorded
    and I cannot read it", UNRECORDED says "nothing was recorded at all", and
    they send the operator to different places.
    """
    if err is None:
        return UNRECORDED
    text = str(err).strip().lower()
    if not text:
        return UNRECORDED
    if key_health.looks_like_auth_error(text):
        return AUTH
    for sign in _QUOTA_SIGNS:
        if sign in text:
            return QUOTA
    for sign in _NETWORK_SIGNS:
        if sign in text:
            return NETWORK
    for sign in _MODEL_SIGNS:
        if sign in text:
            return MODEL
    return UNKNOWN


def cause_line(err: Optional[str]) -> str:
    """`icon reading` — the sentence a card prints above its action line."""
    icon, reading, _action = CAUSE_TEXT[cause_of(err)]
    return f"{icon} {reading}"


def cause_action(err: Optional[str]) -> str:
    """What to DO about this error. Never a key rotation unless a key is what
    the error implicates — the defect this module exists to remove."""
    _icon, _reading, action = CAUSE_TEXT[cause_of(err)]
    return action


# ── What the chain actually did ──────────────────────────────────────
#
# Six outcomes per provider, and the card had one word for all of them. Three
# of the six mean the provider was never contacted.

FAILED = "failed"                  # asked, and it raised
UNPARSEABLE = "unparseable"        # asked, it ANSWERED, the reply did not parse
NO_KEY = "no_key"                  # not asked: no key in the environment
NO_CLIENT = "no_client"            # not asked: the client would not build
SKIPPED_PRIMARY = "skipped_primary"  # not re-asked: it is the one that just failed

#: Which outcomes mean the provider was actually contacted. `SKIPPED_PRIMARY`
#: is deliberately NOT here and is not `not_asked` either — it is the provider
#: that failed on the primary path, so it was asked once, by somebody else.
#: Counting it as a fresh attempt would double-count it; counting it as never
#: asked would erase the failure the whole alert is about.
_CONTACTED = frozenset({FAILED, UNPARSEABLE})
_NOT_CONTACTED = frozenset({NO_KEY, NO_CLIENT})


def attempt_summary(walk: Optional[list]) -> dict:
    """Count a recorded chain walk.

    ``walk`` is a list of ``{"provider": str, "outcome": str, "error": str}``.
    A walk that is absent or is not a list answers ``readable=False`` and no
    counts at all — an unrecorded walk is not a walk of length zero, and the
    card must be able to tell those apart. A walk of length zero IS a real
    reading: it means the chain had no step to take.
    """
    if not isinstance(walk, list):
        return {"readable": False}
    contacted = 0
    not_contacted = 0
    primary = 0
    unclassified = 0
    for step in walk:
        if not isinstance(step, dict):
            unclassified += 1
            continue
        outcome = str(step.get("outcome") or "")
        if outcome in _CONTACTED:
            contacted += 1
        elif outcome in _NOT_CONTACTED:
            not_contacted += 1
        elif outcome == SKIPPED_PRIMARY:
            primary += 1
        else:
            unclassified += 1
    return {
        "readable": True,
        "total": len(walk),
        "contacted": contacted,
        "not_contacted": not_contacted,
        "skipped_primary": primary,
        "unclassified": unclassified,
        # ONE NUMBER FOR "was anybody asked", because two readers need it and
        # `contacted + skipped_primary` computed twice is two answers waiting
        # to drift. The primary belongs in it: it was asked, one frame up, and
        # its failure is what the whole alert is about.
        "asked": contacted + primary,
    }


def chain_coverage_sentence(walk: Optional[list], streak: int,
                            aged: str = "") -> str:
    """The card's headline: what was tried, and how many times it has happened.

    NOT `coverage_sentence`. `bot/core/symbol_rest.py` already defines a
    function by that name, it is in `tests/unreachable_functions_baseline.txt`
    because nothing calls it, and the reachability sweep counts IDENTIFIERS —
    so three call sites of this one acquitted that one, and the ratchet failed
    on a stale entry that was not stale. A dead function quietly leaving the
    baseline is the quiet half of that gate. `anomaly_scope.is_due` carries
    the same note for the same reason, and states the remedy: the cheap fix is
    not to collide.

    Four shapes, because four different things can be true and the old card
    said "Every LLM provider has failed" for all of them.

    ``aged`` is an already-formatted duration (" (~9 min)") and rides HERE
    rather than being appended by the caller, so it lands beside the streak it
    qualifies instead of after whichever sentence happened to come last.
    """
    plural = "analyses" if streak != 1 else "analysis"
    tail = f"for <b>{streak}</b> {plural} in a row{aged}"
    summary = attempt_summary(walk)
    if not summary["readable"]:
        # No walk on record. Say the streak — which IS measured — and claim
        # nothing about how many providers stand behind it.
        return (f"The analyzer fell back to the rule engine {tail}. "
                "Which providers were tried was not recorded.")
    not_contacted = summary["not_contacted"]
    asked = summary["asked"]
    if asked == 0:
        # The expensive case, and the old card's words were furthest from it:
        # nothing was contacted at all, so nothing "failed".
        chain = ("the only provider in the chain has no usable key."
                 if summary["total"] == 1 else
                 f"none of the {summary['total']} providers in the chain has "
                 "a usable key.")
        return (f"<b>No LLM provider could be contacted at all</b> — {chain} "
                f"The analyzer fell back to the rule engine {tail}.")
    if not_contacted == 0:
        if asked == 1:
            return (f"The only provider in the chain was tried and failed "
                    f"{tail}.")
        return f"Every provider that was tried ({asked}) failed {tail}."
    total = asked + not_contacted
    tried = (f"1 of {total} providers was tried, and it failed {tail}."
             if asked == 1 else
             f"{asked} of {total} providers were tried, and all of them "
             f"failed {tail}.")
    rest = ("The other has no usable key and was never contacted."
            if not_contacted == 1 else
            f"The other {not_contacted} have no usable key and were never "
            "contacted.")
    return f"{tried} {rest}"
