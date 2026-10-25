"""Plain-text reading. Dates and amounts. No verdict.

The sentences are a template. ``ai_written`` on the reading stays false.
"""

from __future__ import annotations

from bot.silent_fee.address import NOT_AN_ADDRESS, SECRET_REPLY, WINDOW_REPLY

_FOOTER = "Information, not advice."
REVOKE = "https://app.hyperliquid.xyz/builderCodes"


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _short(addr: str) -> str:
    if not isinstance(addr, str) or len(addr) < 12:
        return addr if isinstance(addr, str) else ""
    return addr[:6] + "…" + addr[-4:]


def _advisory_line(addr: str, hit: dict, first_seen, code=None) -> str:
    versions = hit.get("versions") or []
    span = ""
    if len(versions) == 1:
        span = f", {hit.get('package')} {versions[0]}"
    elif len(versions) >= 2:
        span = f", {hit.get('package')} {versions[0]}–{versions[-1]}"
    line = (
        f"{_short(addr)} appears in {hit.get('id')} "
        f"(published {hit.get('published')}){span}."
    )
    if isinstance(code, str) and code and "referral_code" in (hit.get("matched_on") or []):
        line += f" Referral code {code}."
    if isinstance(first_seen, str) and first_seen:
        line += f" First fee date {first_seen}."
    return line


def render_text(reading: dict) -> str:
    """One Telegram reply. A refusal is its own sentence."""
    if not isinstance(reading, dict):
        return "Unreadable"
    if reading.get("available") is False:
        return "not available yet"
    refused = reading.get("refused")
    if refused == "secret":
        return SECRET_REPLY
    if refused == "address":
        return NOT_AN_ADDRESS
    if refused == "window":
        return WINDOW_REPLY
    lines: list[str] = [
        "Silent fee check. Read-only. Nothing was signed.",
    ]
    if reading.get("ai_written") is True:
        lines.append("The summary below is AI-written.")
    lines.extend([
        f"Network: {reading.get('network')}",
        f"Address: {_short(str(reading.get('queried') or ''))}",
    ])
    role = _dict(reading.get("role"))
    if role.get("read") is True and isinstance(role.get("role"), str):
        lines.append(f"Role: {role['role']}")
        master = role.get("master")
        queried = reading.get("queried")
        if isinstance(master, str) and master and master != queried:
            lines.append(f"Master: {_short(master)}")
    elif role.get("read") is False:
        lines.append("Role: not read.")
    subs = _dict(reading.get("sub_accounts"))
    if subs.get("read") is True:
        addrs = _list(subs.get("addresses"))
        lines.append(f"Sub-accounts: {len(addrs)}")
    elif subs.get("read") is False:
        lines.append("Sub-accounts: not read.")
    headline = _dict(reading.get("headline"))
    if headline.get("signal") == "headroom":
        lines.append(
            "Headroom "
            f"{headline.get('headroom_bp')} bp "
            f"({headline.get('headroom_per_10k_usdc')} USDC per 10,000 USDC notional), "
            f"cap {headline.get('approved_cap_bp')} bp, "
            f"charged {headline.get('charged_bp')} bp, "
            f"builder {_short(str(headline.get('builder') or ''))}."
        )
    else:
        lines.append("Headroom: not read. The charged rate was not read.")
    for account in reading.get("accounts") or []:
        if not isinstance(account, dict):
            continue
        lines.append(f"Account {_short(str(account.get('address') or ''))}")
        builders = account.get("approved_builders")
        if account.get("approved_builders_read") is False:
            lines.append("Approved builders: not read.")
        elif isinstance(builders, list) and not builders:
            lines.append("Approved builders: 0")
        elif isinstance(builders, list):
            for row in builders:
                if not isinstance(row, dict):
                    continue
                if row.get("cap_read") is True:
                    lines.append(
                        f"Builder {_short(str(row.get('builder') or ''))}: "
                        f"cap {row.get('cap_bp')} bp ({row.get('cap_percent')}%), "
                        f"{row.get('cap_per_10k_usdc')} USDC per 10,000 USDC notional."
                    )
                else:
                    lines.append(
                        f"Builder {_short(str(row.get('builder') or ''))}: cap not read."
                    )
        referral = _dict(account.get("referral"))
        if referral.get("read") is False:
            lines.append("Referrer: not read.")
        else:
            code = referral.get("code")
            referrer = referral.get("referrer")
            if isinstance(code, str) and code:
                who = f" code {code}"
                if isinstance(referrer, str) and referrer:
                    who += f" ({_short(referrer)})"
                lines.append("Referrer:" + who)
            else:
                lines.append("Referrer: none on record.")
        fees = _dict(account.get("user_fees"))
        if fees.get("read") is False:
            lines.append("Fee schedule: not read.")
        else:
            cross = fees.get("user_cross_rate")
            discount = fees.get("active_referral_discount")
            lines.append(
                "Fee schedule: "
                f"cross {cross if cross is not None else 'absent'}, "
                f"referral discount {discount if discount is not None else 'absent'}."
            )
        if account.get("builder_fee_complete") is True:
            lines.append(
                f"Builder fees over {reading.get('window_days')} days: "
                f"{account.get('builder_fee_usdc')} USDC "
                f"across {account.get('builder_fee_fills')} fills."
            )
        else:
            lines.append(
                "Builder fees read so far: "
                f"{account.get('builder_fee_usdc')} USDC. "
                "The window is incomplete, so this is not the whole sum."
            )
        if account.get("attribution") == "off":
            lines.append(
                "Per-builder attribution is off. "
                "Fills do not name the builder."
            )
        elif account.get("attribution") == "partial":
            lines.append(
                f"Unattributed builder fees: {account.get('unattributed_usdc')} USDC. "
                "Those fills were not assigned to a builder."
            )
        for paid in account.get("builders_paid") or []:
            if not isinstance(paid, dict):
                continue
            if paid.get("headroom_bp") is not None:
                lines.append(
                    f"Charged {_short(str(paid.get('builder') or ''))}: "
                    f"headroom {paid.get('headroom_bp')} bp "
                    f"({paid.get('headroom_per_10k_usdc')} USDC per 10,000 USDC notional), "
                    f"paid {paid.get('paid_usdc')} USDC, {paid.get('n_fills')} fills, "
                    f"first {paid.get('first_seen') or 'unknown'}, "
                    f"last {paid.get('last_seen') or 'unknown'}."
                )
            else:
                lines.append(
                    f"Charged {_short(str(paid.get('builder') or ''))}: "
                    f"paid {paid.get('paid_usdc')} USDC, {paid.get('n_fills')} fills. "
                    "Headroom not read."
                )
            signed = paid.get("approval_signed_at")
            if isinstance(signed, str):
                lines.append(f"Approval time: {signed}.")
        agents = _dict(account.get("extra_agents"))
        if agents.get("read") is True:
            rows = _list(agents.get("agents"))
            lines.append(f"Named agents: {len(rows)}")
        elif agents.get("reason") == "undocumented_read_off":
            lines.append("Named agents: not read.")
    table = _dict(reading.get("advisory_table"))
    if table.get("read") is False:
        lines.append("The advisory table could not be read, so there is no match result.")
    else:
        ids = _str_list(table.get("ids"))
        lines.append("Seeded advisories checked: " + (", ".join(ids) if ids else "none") + ".")
        notices = _list(reading.get("notices"))
        if not notices:
            lines.append("No seeded advisory names the builders or the referrer on this account.")
        else:
            for notice in notices:
                if not isinstance(notice, dict):
                    continue
                hit = notice.get("hit")
                if not isinstance(hit, dict):
                    continue
                label = notice.get("address") or notice.get("code") or ""
                lines.append(_advisory_line(
                    str(label), hit, notice.get("first_seen"), notice.get("code"),
                ))
    lines.append(f"Revoke in your own wallet at {REVOKE}. This check does not sign.")
    lines.append(_FOOTER)
    return "\n".join(lines)
