"""Whose Earn account a stake or a redeem acts on — one reading for every door.

`/stake`, `/stake fixed`, `/unstake` and the three confirm buttons behind
them each built their client with `BitgetV3Client.from_config()` — the
OPERATOR's keys — under an `_is_admin` gate, so a normal trader got a refusal
and the operator got one account. Per-user executors and the
`ExchangeCredentialStore` already existed (a linked user's own keys,
decryptable or not, on a named venue) and nothing on the Earn path asked
them. This module is that ask, made ONCE, so the plan card, the button that
executes it and the sealed record all describe the same account and cannot
drift — the operator-book leak this repo cured in `check_risk`, `playbook`
and `get_portfolio`, kept out of the one path that moves money.

Eight states, because "no account" is five different facts and two of the
eight have a usable account behind them:

  caller           the caller linked Bitget keys this bot can read: THEIR account
  operator         an admin with no linked keys: the operator's account
                   (byte-identical to what /stake always did for the operator)
  operator_absent  an admin, no linked keys, and no operator keys configured
  absent           a non-admin who never linked an account
  unreadable       keys on file that will not decrypt (the master key changed)
  unsupported      linked and readable, and not Bitget — Earn here is Bitget-only
  unavailable      the credential store itself could not be asked
  revoked          an admin explicitly revoked this caller's live access

A linked account wins over the operator's for EVERYONE, admin included: an
admin who brought their own keys stakes their own idle stables, which is the
rule `/livebalance` and `_executor_for` already follow ("executor identity
decides"). The card names the account either way, and the record carries it.

A caller's client is built from the decrypted fields directly and never
through `BitgetV3Client.for_account`, whose documented fallback is the
operator's keys — the one fallback this module exists to make unreachable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

STATES: tuple[str, ...] = (
    "caller", "operator", "operator_absent", "absent", "unreadable",
    "unsupported", "unavailable", "revoked",
)
USABLE: tuple[str, ...] = ("caller", "operator")

#: The owner tag a confirm button carries for the operator's account. A
#: caller's tag is their Telegram id, so a plan built over one account cannot
#: be executed against another by whoever taps it.
OPERATOR_TAG = "op"

#: The one venue whose Earn this bot serves. A caller linked elsewhere is
#: `unsupported`, recorded rather than guessed at.
EARN_VENUE = "bitget"

_KNOWN_STATES = ("readable", "unreadable", "absent")


@dataclass(frozen=True)
class EarnAccount:
    state: str
    client: Any = None
    #: "operator", or the caller's Telegram id. Present on every refusal that
    #: is about a person, so the record can still say whose refusal it was.
    owner: str = ""
    #: `store.fingerprint()` for a caller — a hash, never the key. "" for the
    #: operator, whose keys the store does not hold.
    fingerprint: str = ""
    #: The linked venue when `unsupported`; EARN_VENUE when usable.
    venue: str = ""
    #: The exception CLASS name when `unavailable`. Never its message: a
    #: driver message can carry a host or a config value.
    fault: str = ""

    @property
    def usable(self) -> bool:
        return self.state in USABLE

    @property
    def tag(self) -> str:
        """What a confirm button carries so the tap is checked against the
        plan's account. "" for anything that is not an account."""
        if self.state == "operator":
            return OPERATOR_TAG
        if self.state == "caller":
            return self.owner
        return ""

    def record(self) -> dict:
        """The fields a sealed audit entry carries about the account acted on."""
        return {"account": self.owner or "none", "key": self.fingerprint,
                "venue": self.venue}


def resolve_earn_account(user_id, *, is_admin: bool,
                         operator_client: Callable[[], Any],
                         store=None, revoked: bool = False) -> EarnAccount:
    """The account THIS caller's stake or redeem acts on.

    `operator_client` is the existing `_yield_client` — the operator's signed
    client, or None when no operator keys are configured — handed in as a
    callable so it is built only on the branch that uses it. `store` defaults
    to the process credential store; tests hand in a planted one.
    """
    uid = str(user_id or "")
    if revoked:
        return EarnAccount("revoked", owner=uid)
    try:
        if store is None:
            from bot.core.exchange_credentials import get_credential_store
            store = get_credential_store()
        state = store.credential_state(uid) if uid else "absent"
    except Exception as exc:
        return EarnAccount("unavailable", owner=uid, fault=type(exc).__name__)
    if state not in _KNOWN_STATES:
        # A word this module does not know is not "absent": absent is an
        # invitation to /connect, and this is a store answering in a
        # vocabulary nobody checked.
        return EarnAccount("unavailable", owner=uid, fault=f"state={state!r}")
    if state == "readable":
        try:
            venue = str(store.get_venue(uid) or "").strip().lower()
            if venue != EARN_VENUE:
                return EarnAccount("unsupported", owner=uid, venue=venue)
            creds = store.get(uid) or {}
            fingerprint = str(store.fingerprint(uid) or "")
        except Exception as exc:
            return EarnAccount("unavailable", owner=uid, fault=type(exc).__name__)
        key = str(creds.get("api_key") or "")
        secret = str(creds.get("api_secret") or "")
        if not (key and secret):
            # Readable a moment ago and not now, or a record missing half its
            # fields. A key without a secret cannot sign, and `for_account`
            # would answer that with the operator's keys.
            return EarnAccount("unreadable", owner=uid)
        from bot.core.bitget_v3_client import BitgetV3Client
        client = BitgetV3Client(key, secret, str(creds.get("passphrase") or ""))
        return EarnAccount("caller", client=client, owner=uid,
                           fingerprint=fingerprint, venue=EARN_VENUE)
    if state == "unreadable":
        return EarnAccount("unreadable", owner=uid)
    if not is_admin:
        return EarnAccount("absent", owner=uid)
    client = operator_client()
    if client is None:
        return EarnAccount("operator_absent", owner="operator")
    return EarnAccount("operator", client=client, owner="operator", venue=EARN_VENUE)


def earn_account_line(acct: EarnAccount) -> str:
    """The card's line naming the account a plan is over. Telegram HTML."""
    if acct.state == "caller":
        key = f" (<code>{acct.fingerprint}</code>)" if acct.fingerprint else ""
        return f"Account: <b>your linked Bitget</b>{key}"
    if acct.state == "operator":
        return "Account: <b>the operator's Bitget</b>"
    raise ValueError(f"no account to name: {acct.state}")


_CONNECT = ("<code>/connect &lt;api_key&gt; &lt;api_secret&gt; "
            "&lt;passphrase&gt;</code>")

#: A tapped button whose owner tag is not the presser's account — or a button
#: from before tags existed. Fail closed, the way `_callback_owner_ok` does:
#: an untagged payload cannot have come from a button this build sent.
EARN_TAG_MISMATCH = (
    "🔒 This plan was built over a different account than yours, or by a "
    "build that did not tag its buttons — run <code>/stake</code> or "
    "<code>/unstake</code> yourself for a plan over your own account. "
    "Nothing was moved.")


def earn_refusal(acct: EarnAccount) -> str:
    """Why nothing will move, for each state that is not an account. Every
    sentence ends by saying so, because a request to act that is answered at
    all must say whether anything acted. Asking for a refusal on a usable
    account raises: a refusal for an account that could act is a narration."""
    s = acct.state
    if s == "absent":
        return ("🔒 <code>/stake</code> and <code>/unstake</code> act on YOUR "
                "exchange account, and none is linked for you. Link your own "
                f"Bitget with {_CONNECT} (in a private chat). Nothing was moved.")
    if s == "unreadable":
        return ("🔴 Your exchange keys are on file but this bot cannot read them "
                "— its encryption key changed, and nothing you did caused it. "
                f"Re-link with {_CONNECT} and try again. Nothing was moved.")
    if s == "unsupported":
        venue = acct.venue or "another venue"
        return ("🟡 Earn through this bot is Bitget-only today, and your linked "
                f"account is <b>{venue}</b> — {venue} Earn is not served, which "
                "is recorded rather than guessed at. Nothing was moved.")
    if s == "unavailable":
        return ("🔴 The credential store could not be asked, so which account "
                "this would act on is unknown — not a missing link. Try again "
                "in a moment. Nothing was moved.")
    if s == "revoked":
        return ("🔒 An admin revoked your live access, and staking moves real "
                "funds, so it is covered by that revoke. Nothing was moved.")
    if s == "operator_absent":
        return ("🔴 No operator Bitget keys configured — <code>/setexchange</code> "
                "first. Nothing was moved.")
    raise ValueError(f"no refusal for a usable account: {s}")
