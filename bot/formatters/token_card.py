"""The /rclaw card: the $RCLAW record rendered, with nothing invented.

Three values, not two, for every field a chain can leave empty: a ``null``
authority is *none (revoked)*, which is the fact a holder wants; a ``null``
presale field is *not announced yet* (a ``null`` sale link: *not created
yet*, and a ``null`` lock field: *not created yet*); a value of a shape the
record did not promise, or a link off the one host it may name, is
*unreadable*. None of them is ever a blank cell or ``None``.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

NOT_ANNOUNCED = "not announced yet"
AUTHORITY_NONE = "none (revoked)"
UNREADABLE = "unreadable"
SALE_LINK_NONE = ("not created yet. It is posted here and on the website's /token page "
                  "before the sale opens; a sale link anywhere else is not ours.")

# The only sale link the card will print: https on smithii.io or a subdomain,
# written plainly. A sale link is what a phishing clone forges first, so
# anything else is unreadable, never a link. The website holds the same rule
# (app/public/js/token-page.js, SALE_URL); tests/fixtures/sale_url_cases.json
# holds both to it.
SALE_URL = re.compile(r"https://(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)*smithii\.io(?:/[A-Za-z0-9._~%/?#=&+-]*)?")
SALE_URL_MAX = 300

# The presale terms, in the order a buyer reads them: (record key, label).
# Every key is a key of the record's presale block, and the test holds this
# list to the record both ways, so a term the record announces is printed.
TERMS = (
    ("date", "Date"),
    ("price", "Price"),
    ("venue", "Venue"),
    ("allocation_tokens", "For sale"),
    ("hard_cap", "Hard cap"),
    ("per_wallet", "Per wallet"),
    ("whitelist", "Whitelist"),
    ("claim", "Claim"),
    ("refunds", "Refunds"),
    ("after_sale", "After the sale"),
)

# The only certificate link the card will print for a lock: Smithii's vesting
# certificate for a Solana lock, a lowercase slug and nothing after it. The
# website holds the same rule (token-page.js, CERTIFICATE_URL), and
# tests/fixtures/lock_certificate_cases.json holds both to it.
CERTIFICATE_URL = re.compile(r"https://tools\.smithii\.io/vesting/solana/[a-z0-9-]{1,30}")
BASE58_ADDRESS = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
SOLSCAN_ACCOUNT = "https://solscan.io/account/"
# An unlock instant as the record writes it: a whole minute in UTC.
UNLOCK_AT = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):00Z")
LOCK_NONE = "not created yet"
LOCKS_NOTE = ("Each lock is an account of Smithii's Token Vesting program, which has no "
              "instruction to cancel or edit a lock: only the wallet named can claim, and only "
              "on or after each date. Smithii can upgrade that program, and no audit of the "
              "version running is established.")
# Shorter than the website's sentence: the card is one Telegram message, and
# the multisig's own account (which holds nothing) is left to /token.
MULTISIG_NOTE = ("Moving tokens out, or changing these rules, needs the approvals shown and then "
                 "the time lock's wait; the Squads v4 program cannot be upgraded. Custody with a "
                 "delay, not a lock.")
_MISSING = object()


def authority_text(value: Any) -> str:
    """``null`` on an SPL mint means the authority was revoked: say that."""
    if value is None:
        return AUTHORITY_NONE
    if isinstance(value, str) and value.strip():
        return f"held by {html.escape(value)}"
    return UNREADABLE


def supply_text(raw: Any) -> str:
    """``"1000000000"`` -> ``1,000,000,000``; anything else raises."""
    text = str(raw)
    if not text.isdigit():
        raise ValueError("supply_tokens is not a whole number")
    return f"{int(text):,}"


def is_sale_url(value: Any) -> bool:
    """True only for a link the card may print: see ``SALE_URL``."""
    return (isinstance(value, str) and len(value) <= SALE_URL_MAX
            and SALE_URL.fullmatch(value) is not None)


def _term_text(presale: Mapping[str, Any], key: str, symbol: str) -> str:
    """A presale term in three values: absent is unreadable, ``null`` is not announced."""
    if key not in presale:
        return UNREADABLE
    value = presale[key]
    if value is None:
        return NOT_ANNOUNCED
    if key == "allocation_tokens":
        text = str(value)
        # isascii: str.isdigit() also accepts "²", which int() then refuses.
        return f"{int(text):,} {html.escape(symbol)}" if text.isascii() and text.isdigit() else UNREADABLE
    return html.escape(str(value))


def _sale_link_text(presale: Mapping[str, Any]) -> str:
    if "sale_url" not in presale:
        return UNREADABLE
    value = presale["sale_url"]
    if value is None:
        return SALE_LINK_NONE
    if not is_sale_url(value):
        return UNREADABLE
    url = html.escape(value)
    return f'<a href="{url}">{url}</a>'


# A sale-window instant as the record writes it: a whole minute in UTC. The
# website reads the same window (token-page.js, presalePhase), and
# tests/fixtures/presale_phase_cases.json holds both to one answer.
WINDOW_AT = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):00Z")


def _window_at(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or WINDOW_AT.fullmatch(value) is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:          # 2026-02-30: the shape of a date that does not exist
        return None


def _window(presale: Mapping[str, Any]) -> Optional[tuple[datetime, datetime]]:
    """(opens, closes), or None when the window cannot be read: either
    instant absent or malformed, or a close not after the open."""
    opens = _window_at(presale.get("opens_at"))
    closes = _window_at(presale.get("closes_at"))
    if opens is None or closes is None or closes <= opens:
        return None
    return opens, closes


def _window_text(value: str) -> str:
    m = WINDOW_AT.fullmatch(value)
    assert m is not None
    return f"{m.group(1)} {m.group(2)} UTC"


def presale_phase(presale: Mapping[str, Any], now: Optional[datetime] = None) -> Optional[str]:
    """Where an ANNOUNCED presale stands: ``"announced"`` (before it opens),
    ``"live"`` or ``"ended"``, from the record's ``opens_at`` / ``closes_at``
    and the clock. A window that cannot be read is the record's own status,
    ``"announced"``, which is true of a sale that has opened too; any other
    status has no phase (None) and the card prints that status. The card said
    "announced" for the whole sale while the sale was open."""
    if presale.get("status") != "announced":
        return None
    window = _window(presale)
    if window is None:
        return "announced"
    opens, closes = window
    at = now if now is not None else datetime.now(timezone.utc)
    if at < opens:
        return "announced"
    return "live" if at < closes else "ended"


def presale_lines(presale: Mapping[str, Any], symbol: str = "RCLAW",
                  now: Optional[datetime] = None) -> list[str]:
    status = presale.get("status")
    phase = presale_phase(presale, now)
    if status == "coming_soon":
        head = "🗓 <b>Presale: coming soon</b>"
    elif phase == "live":
        head = "🟢 <b>Presale: live now</b>"
    elif phase == "ended":
        head = "🗓 <b>Presale: ended</b>"
    elif status == "announced":
        head = "🗓 <b>Presale: announced</b>"
    elif isinstance(status, str) and status.strip():
        head = f"🗓 <b>Presale: {html.escape(status.replace('_', ' '))}</b>"
    else:
        head = f"🗓 <b>Presale: status {UNREADABLE}</b>"
    lines = [head]
    if phase == "live":
        lines.append(f"Open until {_window_text(presale['closes_at'])}, or until the hard cap is "
                     "reached. Buy only through the sale link below.")
    elif phase == "ended":
        lines.append(f"Closed at {_window_text(presale['closes_at'])}, or earlier if the hard cap "
                     "was reached. Buyers claim 100% of what they bought on the sale page.")
    elif phase == "announced" and _window(presale) is not None:
        # An unreadable window says nothing about when it opens.
        lines.append(f"Opens {_window_text(presale['opens_at'])}.")
    lines.extend(f"{label}: {_term_text(presale, key, symbol)}" for key, label in TERMS)
    lines.append(f"Sale link: {_sale_link_text(presale)}")
    if status == "coming_soon":
        lines.append("It will be announced here, on the website's /token page and on the "
                     "project's X account before anything opens.")
    elif status == "announced":
        lines.append("Smithii's sale contract enforces the price, the hard cap, the per-wallet "
                     "limits and the claim; the soft cap and everything after the sale are the "
                     "team's commitments, not the contract's. We never DM first and never ask "
                     "for a seed phrase.")
    return lines


def is_certificate_url(value: Any) -> bool:
    """True only for a link the card may print: see ``CERTIFICATE_URL``."""
    return isinstance(value, str) and CERTIFICATE_URL.fullmatch(value) is not None


def _lock_address(value: Any, *, link: bool) -> str:
    if value is _MISSING:
        return UNREADABLE
    if value is None:
        return LOCK_NONE
    if not isinstance(value, str) or BASE58_ADDRESS.fullmatch(value) is None:
        return UNREADABLE
    code = f"<code>{value}</code>"
    return f'<a href="{SOLSCAN_ACCOUNT}{value}">{code}</a>' if link else code


def _certificate(value: Any) -> str:
    if value is _MISSING:
        return UNREADABLE
    if value is None:
        return LOCK_NONE
    if not is_certificate_url(value):
        return UNREADABLE
    return f'<a href="{value}">certificate</a>'


def _unlocks(value: Any) -> str:
    """Every unlock as ``2026-10-29 14:00 5%``; one entry of another shape makes the list unreadable."""
    if not isinstance(value, list) or not value:
        return UNREADABLE
    parts = []
    for u in value:
        at = u.get("at") if isinstance(u, dict) else None
        pct = u.get("percent") if isinstance(u, dict) else None
        m = UNLOCK_AT.fullmatch(at) if isinstance(at, str) else None
        if m is None or not isinstance(pct, int) or isinstance(pct, bool) or not 1 <= pct <= 100:
            return UNREADABLE
        parts.append(f"{m.group(1)} {m.group(2)} {pct}%")
    return " · ".join(parts)


def _lock_lines(item: Any, symbol: str) -> list[str]:
    it = item if isinstance(item, dict) else {}
    bucket = it.get("bucket")
    name = html.escape(bucket) if isinstance(bucket, str) and bucket else UNREADABLE
    tokens = str(it.get("tokens", ""))
    amount = (f"{int(tokens):,} {html.escape(symbol)}"
              if tokens.isascii() and tokens.isdigit() else UNREADABLE)
    schedule = it.get("schedule")
    sched = html.escape(schedule) if isinstance(schedule, str) and schedule else UNREADABLE
    created = [it.get(k, _MISSING) for k in ("vesting_account", "claim_wallet", "certificate_url")]
    if all(v is None for v in created):
        where = f"Lock: {LOCK_NONE}"
    else:
        where = (f"Lock: {_lock_address(created[0], link=True)} · "
                 f"claimed by {_lock_address(created[1], link=False)} · {_certificate(created[2])}")
    return [
        f"<b>{name}</b>: {amount} · {sched}",
        f"Unlocks (UTC): {_unlocks(it.get('unlocks'))}",
        where,
    ]


def locks_lines(record: Mapping[str, Any], symbol: str = "RCLAW") -> list[str]:
    """The time locks, from the record's locks block, in three values.

    A record without the block says the locks could not be read; ``null``
    says none is created yet; each lock then prints field by field.
    """
    head = "🔒 <b>Locked allocations</b>"
    if "locks" not in record:
        return [f"{head}: {UNREADABLE}"]
    locks = record["locks"]
    if locks is None:
        return [f"{head}: {LOCK_NONE}"]
    items = locks.get("items") if isinstance(locks, dict) else None
    if not isinstance(items, list) or not items:
        return [f"{head}: {UNREADABLE}"]
    lines = [head]
    for item in items:
        lines.extend(_lock_lines(item, symbol))
    lines.append(LOCKS_NOTE)
    checked = locks.get("verified_at")
    if isinstance(checked, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked):
        lines.append(f"Checked against the chain on {checked}.")
    return lines


def _quorum(ms: Mapping[str, Any]) -> str:
    """``2 of 3 members`` from the threshold and the member list; anything else is unreadable."""
    n = ms.get("threshold")
    members = ms.get("members")
    if (not isinstance(members, list)
            or not all(isinstance(k, str) and BASE58_ADDRESS.fullmatch(k) for k in members)):
        return UNREADABLE
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= len(members):
        return UNREADABLE
    return f"{n} of {len(members)} members"


def _time_lock(seconds: Any) -> str:
    """The delay in hours when it is whole hours, else seconds; 0 is a real "none"."""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0:
        return UNREADABLE
    if seconds == 0:
        return "none: an approved transfer executes at once"
    return f"{seconds // 3600} hours" if seconds % 3600 == 0 else f"{seconds} seconds"


def _holds(ms: Mapping[str, Any], symbol: str) -> str:
    """The vault's total, then each bucket; one bucket of another shape makes it unreadable."""
    total = ms.get("holds_tokens")
    buckets = ms.get("buckets")
    if not (isinstance(total, str) and total.isascii() and total.isdigit()):
        return UNREADABLE
    if not isinstance(buckets, list) or not buckets:
        return UNREADABLE
    parts = []
    for b in buckets:
        name = b.get("bucket") if isinstance(b, dict) else None
        tokens = b.get("tokens") if isinstance(b, dict) else None
        if not (isinstance(name, str) and name and isinstance(tokens, str)
                and tokens.isascii() and tokens.isdigit()):
            return UNREADABLE
        parts.append(f"{html.escape(name)} {int(tokens):,}")
    return f"{int(total):,} {html.escape(symbol)} ({' · '.join(parts)})"


def multisig_lines(record: Mapping[str, Any], symbol: str = "RCLAW") -> list[str]:
    """The treasury multisig, from the record's multisig block, in three values.

    A record without the block says it could not be read; ``null`` says it
    is not created yet; each field then prints on its own.
    """
    head = "🏦 <b>Treasury multisig</b>"
    if "multisig" not in record:
        return [f"{head}: {UNREADABLE}"]
    ms = record["multisig"]
    if ms is None:
        return [f"{head}: {LOCK_NONE}"]
    if not isinstance(ms, dict):
        return [f"{head}: {UNREADABLE}"]
    lines = [
        head,
        f"Holds: {_holds(ms, symbol)}",
        f"Approvals: {_quorum(ms)} · time lock: {_time_lock(ms.get('time_lock_seconds'))}",
        f"Vault: {_lock_address(ms.get('vault', _MISSING), link=True)}",
        MULTISIG_NOTE,
    ]
    checked = ms.get("verified_at")
    if isinstance(checked, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked):
        lines.append(f"Checked against the chain on {checked}.")
    return lines


def env_mint_line(record_mint: str, env_mint: Optional[str]) -> str:
    """For the operator: what the tier gate's ``RCLAW_MINT`` names, in three values."""
    if not env_mint:
        return "🔧 RCLAW_MINT is unset in this bot's env: the tier gate is inert."
    if env_mint == record_mint:
        return "🔧 RCLAW_MINT matches this mint."
    return ("🔧 RCLAW_MINT in this bot's env is a different address — the tier gate "
            "reads that one, not this mint.")


def render_token_card(record: Mapping[str, Any], *, operator: bool = False,
                      env_mint: Optional[str] = None,
                      now: Optional[datetime] = None) -> str:
    mint = html.escape(str(record["mint"]))
    decimals = record["decimals"]
    if not isinstance(decimals, int) or isinstance(decimals, bool):
        raise ValueError("decimals is not an integer")
    lines = [
        f"🪙 <b>${html.escape(str(record['symbol']))} — {html.escape(str(record['name']))}</b>",
        f"Chain: {html.escape(str(record['chain']))} ({html.escape(str(record['cluster']))}) "
        f"· {html.escape(str(record['standard']))}",
        f"Mint: <code>{mint}</code>",
        f"Supply: {supply_text(record['supply_tokens'])} ({decimals} decimals) · "
        f"fixed: mint authority {authority_text(record['mint_authority'])}",
        f"Freeze authority: {authority_text(record['freeze_authority'])}",
    ]
    created = record.get("created_at")
    if isinstance(created, str) and len(created) >= 10:
        lines.append(f"Created: {html.escape(created[:10])} (UTC)")
    elif created is not None:
        lines.append(f"Created: {UNREADABLE}")
    lines.append(f'🔗 <a href="{html.escape(str(record["explorer"]))}">View on Solscan</a>')
    lines.append("")
    lines.extend(presale_lines(record["presale"], str(record["symbol"]), now))
    lines.append("")
    lines.extend(locks_lines(record, str(record["symbol"])))
    lines.append("")
    lines.extend(multisig_lines(record, str(record["symbol"])))
    lines.append("")
    lines.append(f"⚠️ Only this mint address is ${html.escape(str(record['symbol']))}. "
                 "Any other address using the name is not ours.")
    if operator:
        lines.append("")
        lines.append(env_mint_line(str(record["mint"]), env_mint))
    return "\n".join(lines)
