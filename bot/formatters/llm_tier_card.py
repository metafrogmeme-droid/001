"""The /llmtiers card: where each tier runs, and whether the operator's
instruction actually took effect.

THE QUESTION THE CARD EXISTS TO ANSWER is "I set LLM_TIER_CHAT_PROVIDER — did
it bind?", and the version this replaces could not answer it. It printed

    Source: tier-routed | <reason from DEFAULT_TIER_ROUTING>

where `tier-routed` meant `resolved_config != primary_config` — which is true
for a tier the operator pinned by hand AND for a tier nobody has ever touched,
because the default routing table also differs from primary. Both read the
same. So an operator who set three of the four tiers and forgot the fourth saw
four identical-looking lines, and the forgotten one was invisible.

The `reason` was worse: it came from `DEFAULT_TIER_ROUTING[tier]` and printed
unconditionally, so a tier pinned to the in-house model was justified with
Groq's rationale — "Groq is the fastest + genuinely-free inference" — an
argument for a route that is not in force, attached to a value that is.

WHY A RENDERER AND NOT A FIX IN PLACE. The card was built inline in the
handler, which is why none of this was ever asserted. CLAUDE.md's rule is that
a source scan cannot tell "present" from "reached" — #999 shipped a card that
rendered zero times — and the seam is what makes MUST_SAY / MUST_NOT_SAY
possible. Every fact here arrives as data; nothing in this module resolves,
reads the environment, or decides.

WHAT THE ICONS CLAIM. A green tick says "this tier will answer" as loudly as
any sentence, so it is reserved for a tier whose credential is actually known
good AND whose configuration was applied. `keyless_remote` — no key, endpoint
not on this machine — is an observation and gets a warning, not a verdict: the
endpoint may be open. It says what was seen.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Iterable, Sequence

SEP = "─" * 16

#: How each `LLMConfig.key_state()` reads to a person, and whether it is an
#: unqualified pass. `keyless_remote` is deliberately NOT a pass: the tick that
#: used to sit there came from `is_configured()`, which returns True for
#: RUNECLAW unconditionally, so it was printed without anything being checked.
#:
#: IMPORTED, not restated. Three surfaces report this state and two had already
#: drifted; a private copy here is how the next one gets a tick back.
from bot.llm.provider import KEY_STATE_TEXT as _KEY_STATE  # noqa: E402

#: `LLMConfig.source` in words. These are the branches `resolve_tier_config`
#: actually takes, stamped by that function rather than re-derived here.
_SOURCE = {
    "runtime": "runtime override (/settier)",
    "env": "pinned by env",
    "admin-table": "admin premium table",
    "default-table": "default routing table",
    "user-tier": "per-user tier table",
    "primary": "primary LLM_PROVIDER",
    "blocked": "nothing to route to",
}


@dataclass(frozen=True)
class TierRow:
    """One tier's resolved facts. Every field is observed, never inferred."""

    tier: str
    provider: str
    model: str
    #: The `source` stamp from `resolve_tier_config` — which branch produced
    #: this config. "" when the caller could not determine it, which renders as
    #: unknown rather than as a guess.
    source: str = ""
    #: `LLMConfig.key_state()`. "" renders as unknown.
    key_state: str = ""
    #: The env var that names this tier, and the value the operator set. Both
    #: empty when the operator has not pinned this tier at all — which is a
    #: fact the card must state, not a blank to skip over.
    env_var: str = ""
    env_value: str = ""
    #: Why an env value that IS set did not take effect. "" when it did.
    ignored_reason: str = ""
    #: The routing table's rationale — printed ONLY when the route came from
    #: that table, because an argument for a route that is not in force is a
    #: fabricated justification for a real value.
    table_reason: str = ""
    #: The env var that carries this provider's key, so a `keyless_remote`
    #: tier can name its own remedy instead of a generic one.
    key_env: str = ""


def _icon_and_note(row: TierRow) -> tuple[str, str]:
    """The tier's headline icon. An ignored instruction outranks a healthy
    key: the tier answers, and it answers from somewhere the operator did not
    ask for, which is the more surprising fact."""
    if row.env_value and row.source != "env":
        return "⚠️", "override not applied"
    if not row.key_state:
        return "❔", "credential unknown"
    icon, _ = _KEY_STATE.get(row.key_state, ("❔", "unknown"))
    return icon, ""


def render_tier_card(rows: Sequence[TierRow], *,
                     unbound_env: Iterable[str] = (),
                     title: str = "<b>LLM tier routing</b>",
                     audience: str = "admin") -> str:
    """Render the card. Pure: no env reads, no resolution, no I/O.

    `title` is TRUSTED MARKUP, because the i18n catalog already ships it with
    its own `<b>` wrapper — escaping it printed the tags to the reader. It is a
    resource string from this repo, never anything a user supplied.
    """
    if not rows:
        # An empty card is not an empty configuration, and must not read as
        # one. Absent is never a measurement.
        return (f"\U0001f3af {title}\n{SEP}\n"
                "❔ <b>No tier information available.</b> The routing "
                "could not be read, which is not the same as there being none "
                "— nothing below should be taken as the live configuration.")

    out = [f"\U0001f3af {title}",
           f"<i>Resolved as they are for {html.escape(audience)} calls.</i>",
           SEP, ""]

    for row in rows:
        icon, note = _icon_and_note(row)
        head = f"{icon} <b>{html.escape(row.tier.upper())}</b>"
        if note:
            head += f" — <i>{html.escape(note)}</i>"
        out.append(head)
        out.append(f"- Provider: <code>{html.escape(row.provider or 'unknown')}</code>")
        out.append(f"- Model: <code>{html.escape(row.model or 'unknown')}</code>")

        source_txt = _SOURCE.get(row.source, "")
        if source_txt:
            line = f"- Routed by: {source_txt}"
        else:
            line = "- Routed by: <i>unknown</i>"
        out.append(line)

        # The table's rationale, ONLY when the table is what routed this tier.
        if row.table_reason and row.source in ("default-table", "admin-table",
                                               "user-tier"):
            out.append(f"  <i>{html.escape(row.table_reason)}</i>")

        # The operator's own instruction, and whether it survived.
        if row.env_value:
            if row.source == "env":
                out.append(f"- <code>{html.escape(row.env_var)}"
                           f"={html.escape(row.env_value)}</code> — applied")
            else:
                # NOT a footnote. This is the operator's instruction being
                # dropped, which is exactly what looked like "the variable
                # does not work" for as long as nothing reported it.
                out.append(f"- ⚠️ <b>Your <code>"
                           f"{html.escape(row.env_var)}="
                           f"{html.escape(row.env_value)}</code> was NOT "
                           f"applied.</b>")
                # `ignored_reason` may carry deliberate <code> markup from the
                # resolver, so it is trusted as HTML — it never contains user
                # input, only provider names and env-var names.
                out.append(f"  <i>{row.ignored_reason or 'Reason unknown.'}</i>")
        else:
            # THE GAP THIS CARD WAS BUILT FOR. Three of four tiers pinned and
            # the fourth silently on a default is invisible unless the card
            # says which tiers are unpinned.
            out.append(f"- <i>Not pinned — no <code>"
                       f"{html.escape(row.env_var or 'LLM_TIER_' + row.tier.upper() + '_PROVIDER')}"
                       f"</code> set.</i>")

        state_icon, state_txt = _KEY_STATE.get(
            row.key_state, ("❔", "credential not checked"))
        out.append(f"- Key: {state_icon} {state_txt}")

        # A DIAGNOSIS WITHOUT A REMEDY IS HALF A CARD, and the two bad
        # credential states have DIFFERENT remedies — /setllm stores a key for
        # a provider that has none, which is not what a keyless endpoint behind
        # a tunnel needs. Naming one fix for both would send the operator to
        # the wrong place on whichever half it did not fit.
        if row.key_state == "missing":
            out.append("  <i>Fix with <code>/setllm &lt;provider&gt; "
                       "&lt;key&gt;</code> — validated live, stored encrypted, "
                       "survives redeploys.</i>")
        elif row.key_state == "keyless_remote":
            out.append(f"  <i>If that endpoint wants a key, set <code>"
                       f"{html.escape(row.key_env or 'the provider key env var')}"
                       f"</code> and restart.</i>")
        out.append("")

    unbound = sorted(unbound_env)
    if unbound:
        out.append(SEP)
        out.append("⚠️ <b>Variables that reach nothing</b>")
        for name in unbound:
            out.append(f"- <code>{html.escape(name)}</code>")
        out.append("<i>Only <code>SCAN</code>, <code>THESIS</code>, "
                   "<code>LEARNING</code> and <code>CHAT</code> name a tier. "
                   "Anything else is read by no code at all — set it, "
                   "restart, and the tier stays on its default.</i>")
        out.append("")

    out.append(SEP)
    out.append("<i>Pin a tier with <code>LLM_TIER_&lt;TIER&gt;_PROVIDER</code> "
               "plus that provider's key env var, then restart — the "
               "provider catalog is read at import.</i>")
    return "\n".join(out)


def render_engine_uses(rows: Sequence[TierRow]) -> str:
    """One compact line per tier: what answers, and on what credential.

    THE THIRD RENDERING. `tier_report`'s own docstring says it exists because
    "there are two surfaces and they had already drifted apart" and names
    `/llmstatus` as one of them — but only `/llmtiers` and the web panel were
    moved onto it. `/llmstatus` kept a hand-rolled loop of its own:

        <b>Anthropic key slots</b>
        🟢 .env: sk-ant...abc12345 [valid]
        — engine uses → scan: NOT SET | thesis: NOT SET

    Three things wrong in those two lines.

    THE HEADING CLAIMS ANTHROPIC. "engine uses →" sits inside a block listing
    Anthropic key candidates, so it reads as "of these slots, the engine picked
    this one". Once `LLM_TIER_*_PROVIDER` began binding, SCAN and THESIS
    resolve to the self-hosted model and the line is not about Anthropic at
    all.

    "NOT SET" IS A FINGERPRINT'S IDEA OF KEYLESS. `key_fingerprint()` answers
    "NOT SET" whenever there is no key, so a correctly-configured keyless tier
    printed NOT SET directly beneath a VALID key — which reads as "the key
    isn't being picked up", i.e. a healthy engine reported as broken.
    `key_state()` exists to say this properly and this surface never called it.

    TWO TIERS OF FOUR, unlabelled. LEARNING and CHAT were omitted, and they are
    the ones most likely to differ — an operator pinning SCAN/THESIS/LEARNING
    to the in-house model and leaving CHAT on a hosted default saw nothing
    about CHAT here at all.
    """
    if not rows:
        return ("❔ <b>Tier routing could not be read.</b> <i>This is a failed "
                "read, not an absence of routing — nothing here says which "
                "brain is answering.</i>")

    # NOT "What answers right now". That header promised BEHAVIOUR and every
    # icon under it reports CONFIGURATION: ✅ means a credential is set, and
    # nothing here has called anything. 2026-09-02, live — four green ticks
    # under that heading while every chat call was failing, read by someone who
    # had just been told the AI was unavailable. Colour is a claim, and so is a
    # heading. The brain-health line above this section is what answers the
    # question this one was pretending to.
    # NO GLYPH IN THIS SENTENCE. The first draft explained the tick by
    # printing one, and eight tests correctly failed: they assert that a
    # keyless or unchecked tier shows no pass mark anywhere, and prose is
    # part of anywhere. Explaining a claim by making it is the same defect
    # one level up.
    # SAYS WHAT THE SECTION IS, AND NOTHING THE ROWS ALREADY SAY. Two earlier
    # drafts each tripped a guard that was right: one explained the pass mark
    # by printing one, in a card whose tests assert an unchecked tier shows no
    # pass mark anywhere; the next used the word "credential", which every row
    # already carries and which a test counts to stop the same fact being
    # stated twice. Both guards predate this change and both caught it.
    out = ["<b>How each tier is routed</b>",
           "<i>Configuration, not liveness — whether a tier actually answers "
           "is the brain line above.</i>"]
    for row in rows:
        # The ICON only. `_icon_and_note`'s note is "credential unknown" for an
        # unchecked state, which is the same fact the credential text below
        # already carries — the first draft printed both and read
        # "credential not checked · pinned by env · credential unknown".
        # The override case is the one that adds something, so it is derived
        # here from the same condition rather than round-tripped as prose.
        icon, _ = _icon_and_note(row)
        _, cred = _KEY_STATE.get(row.key_state, ("", "credential not checked"))
        src = _SOURCE.get(row.source, "")
        bits = [f"<code>{html.escape(row.provider or 'unknown')}/"
                f"{html.escape(row.model or 'default')}</code>",
                cred]
        if src:
            bits.append(src)
        if row.env_value and row.source != "env":
            bits.append("<b>override not applied</b>")
        out.append(f"{icon} <code>{html.escape(row.tier.upper())}</code> → "
                   + " · ".join(bits))
    return "\n".join(out)
