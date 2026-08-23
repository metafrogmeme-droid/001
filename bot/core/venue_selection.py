"""Which venues a user has chosen to TRADE on — distinct from connected.

Phase 4 of multi-venue (``docs/MULTI_VENUE_RISK_SPLIT.md``). Phases 0–3 made
per-venue state separable and the caps person-level; this is the selection that
decides where orders actually go, and the flag that decides whether the
selection is honoured at all.

THE DISTINCTION THIS MODULE EXISTS FOR. ``credential_store.list_venues()``
answers "where could you trade" — it is a fact about keys. This answers "where
DO you trade", which is a decision. Collapsing them is the single most
dangerous shortcut available here: it would mean that pasting an API key is
itself an instruction to start trading with it, and nobody would have chosen
that. Connecting is not consenting.

So the default is EMPTY, and empty means single-venue — exactly today's
behaviour, on the one venue the credential store already calls active. A user
who never opens the venue picker sees no change, and a user who connects a
second venue sees no change until they say so.

WHY REFUSING IS THE SAFE DIRECTION HERE, TWICE OVER:

  * A venue cannot be activated unless it is CONNECTED. Checked at read time
    and not only at write time, because credentials get revoked after the
    selection was made — an active venue whose keys stopped decrypting must
    drop out of routing and be REPORTED, not silently skipped. Silently
    skipping is how somebody believes they are trading two venues while one has
    been dead for a week.
  * A venue cannot be deactivated while it holds OPEN POSITIONS. Deactivating
    stops routing, which would leave real positions on a venue the engine no
    longer visits — orphans, self-inflicted. The repo already has a card for
    reading orphans it did not open; manufacturing them is worse. The caller
    gets a refusal naming the count, and closing them first is the operator's
    call to make deliberately.

An unreadable store falls back to SINGLE-VENUE, never to "everything
connected". Every failure path in this module narrows what trades; none widens
it.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Callable, Optional

from bot.utils.atomic_write import atomic_write_json
from bot.utils.paths import state_path

log = logging.getLogger("runeclaw.venue_selection")

#: Anchored at construction — see ``person_peak`` for what a relative durable
#: path costs. Never joined to directly.
_STATE_FILE = "data/venue_selection.json"


class VenueSelectionStore:
    """``{user_id: [venue, ...]}`` — the venues a user chose to trade.

    Empty or absent means "single venue", i.e. today's behaviour. That is not a
    placeholder for a real value; it is the safe state, and it is what every
    error path resolves to.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = str(state_path(path or _STATE_FILE))
        self._sel: dict = {}
        self._lock = threading.Lock()
        self._load()

    # ── persistence ──────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if not os.path.exists(self._path):
                return
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            raw = (data or {}).get("selection") or {}
            if not isinstance(raw, dict):
                return
            for user, venues in raw.items():
                if isinstance(venues, list):
                    self._sel[str(user)] = [str(v).lower().strip()
                                            for v in venues if str(v).strip()]
        except Exception as exc:
            # NOT fatal, and NOT widened: an unreadable selection leaves every
            # user on the single-venue default until it can be read again.
            log.warning("venue selection load skipped (all users stay "
                        "single-venue): %s", exc)

    def _save(self) -> None:
        try:
            atomic_write_json(self._path, {"selection": dict(self._sel)}, indent=None)
        except Exception as exc:
            log.warning("venue selection save skipped: %s", exc)

    # ── reads ────────────────────────────────────────────────────────────

    def raw_selection(self, user_id: str) -> list:
        """What the user chose, WITHOUT checking it is still connected.

        Separate from ``active_venues`` on purpose: the UI has to show a venue
        the user picked whose keys have since stopped working, and a reader
        that only ever sees the filtered list cannot tell "you deselected it"
        from "it stopped working".
        """
        return list(self._sel.get(str(user_id), []))

    def active_venues(self, user_id: str, connected: Optional[Callable] = None) -> tuple:
        """``(venues_to_trade, dropped)`` — the routing set, and what fell out.

        ``dropped`` is returned rather than logged-and-forgotten because it is
        the honest half: a venue the user selected that is no longer connected
        must reach them as a fact, not vanish into a shorter list. A caller that
        ignores it is the "absent is never a measurement" failure with money
        behind it.

        Empty ``venues_to_trade`` means single-venue — the caller uses whatever
        it uses today.
        """
        chosen = self.raw_selection(user_id)
        if not chosen:
            return (), ()
        # BOTH lookups are guarded, not just the default one. The first draft
        # wrapped only the import path, so an injected callable that raised
        # propagated out of a routing read — and a crash here is not a narrow
        # failure, it is an unhandled exception on the path that decides where
        # an order goes. Cannot verify → cannot route.
        try:
            if connected is None:
                from bot.core.exchange_credentials import get_credential_store
                have = set(get_credential_store().list_venues(user_id))
            else:
                have = set(connected(user_id) or ())
        except Exception as exc:
            log.warning("venue selection: connected-venue lookup failed for "
                        "%s (%s) — falling back to single venue", user_id, exc)
            return (), tuple(chosen)
        live = tuple(v for v in chosen if v in have)
        gone = tuple(v for v in chosen if v not in have)
        return live, gone

    # ── writes ───────────────────────────────────────────────────────────

    def set_selection(self, user_id: str, venues, *,
                      connected: Optional[Callable] = None,
                      open_positions: Optional[Callable] = None) -> tuple:
        """``(ok, reason)``. Replace this user's chosen venues.

        Two refusals, both deliberate and both narrowing:

        * an unknown or UNCONNECTED venue cannot be selected — you cannot
          choose to trade somewhere you have not linked;
        * a venue currently holding OPEN POSITIONS cannot be dropped, because
          dropping it stops routing while the positions stay real. The refusal
          names the count so the operator can close them on purpose.
        """
        from bot.core.venue_key import normalize_venue

        uid = str(user_id)
        wanted, bad = [], []
        for v in (venues or []):
            n = normalize_venue(v)
            if not n:
                bad.append(str(v))
            elif n not in wanted:
                wanted.append(n)
        if bad:
            return False, f"not a venue this bot supports: {', '.join(bad)}"

        try:
            if connected is None:
                from bot.core.exchange_credentials import get_credential_store
                have = set(get_credential_store().list_venues(uid))
            else:
                have = set(connected(uid) or ())
        except Exception as exc:
            log.warning("venue selection: cannot verify connections for %s: %s",
                        uid, exc)
            return False, "could not verify which venues are connected"
        missing = [v for v in wanted if v not in have]
        if missing:
            return False, ("connect these before selecting them: "
                           + ", ".join(missing))

        dropping = [v for v in self.raw_selection(uid) if v not in wanted]
        if dropping and open_positions is not None:
            blocked = []
            for v in dropping:
                try:
                    n = int(open_positions(uid, v) or 0)
                except Exception as exc:
                    # UNREADABLE IS NOT ZERO. "Could not check for open
                    # positions" must not read as "there are none" on the step
                    # that would strand them.
                    log.warning("venue selection: open-position check failed "
                                "for %s on %s: %s", uid, v, exc)
                    return False, (f"could not check {v} for open positions — "
                                   "not deselecting it until that read works")
                if n > 0:
                    blocked.append(f"{v} ({n} open)")
            if blocked:
                return False, ("close these positions before deselecting: "
                               + ", ".join(blocked))

        with self._lock:
            if wanted:
                self._sel[uid] = wanted
            else:
                self._sel.pop(uid, None)
            self._save()
        return True, ("single venue" if not wanted else ", ".join(wanted))


_STORE: Optional[VenueSelectionStore] = None
_STORE_LOCK = threading.Lock()


def get_venue_selection_store() -> VenueSelectionStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = VenueSelectionStore()
    return _STORE


# ── the routing decision ─────────────────────────────────────────────────

VALID_MODES = ("off", "shadow", "enforce")

#: Is there a caller that ACTS on `effective` — i.e. does an order actually
#: reach more than one venue?
#:
#: TRUE as of the routing commit: `RuneClawEngine._place_live_order` resolves
#: the decision, picks a venue with `choose_venue`, and routes the order to
#: that venue's executor — or REFUSES, rather than falling back to the default
#: venue, which would place a trade somewhere the person did not select.
#:
#: It exists because for one commit it was False, and that gap was worth
#: naming: with nothing acting on `effective`, `enforce` behaved EXACTLY like
#: `shadow` while reporting that it routed across venues. An operator who set
#: it would have believed the feature was live, read plausible reasons in the
#: log, and been wrong. Keeping the constant means the same claim stays
#: checkable — `test_enforce_is_the_only_mode_that_routes` reads it, so
#: flipping it without a caller fails, and so does removing the caller.
ENFORCE_IMPLEMENTED = True


def routing_decision(user_id: str, *, connected: Optional[Callable] = None,
                     store: Optional[VenueSelectionStore] = None) -> dict:
    """Where this user's orders go, and whether that is being acted on.

    ONE function answers this so a surface cannot disagree with the executor
    about where an order went — the same reason `_is_admin_id` is not copied
    into the proactive monitor.

    Returns ``{mode, venues, dropped, effective, reason}``:

      * ``mode``      — off | shadow | enforce, from config
      * ``venues``    — the user's connected, selected venues
      * ``dropped``   — selected but no longer connected. NEVER silently
                        discarded: a venue that stopped working must reach the
                        user as a fact, or they believe they are trading two
                        while one has been dead for a week.
      * ``effective`` — the venues that will ACTUALLY be routed to. Empty means
                        single-venue, i.e. the caller does what it does today.
      * ``reason``    — why, in words, for the operator surface

    `effective` is empty in every mode except `enforce`, and empty on any
    failure. There is no path through this function where an error widens the
    set of venues that trade.
    """
    from bot.config import CONFIG

    mode = str(getattr(CONFIG, "multi_venue_trading_mode", "shadow") or "").lower()
    if mode not in VALID_MODES:
        # An unrecognised mode is a configuration error, and the safe reading of
        # one is the most restrictive value — not the default, which would let a
        # typo like MULTI_VENUE_TRADING_MODE=enfroce read as `shadow` and look
        # deliberate.
        log.warning("MULTI_VENUE_TRADING_MODE=%r is not one of %s — treating as "
                    "off", mode, VALID_MODES)
        mode = "off"
    if not getattr(CONFIG, "multi_venue_trading_enabled", False):
        mode = "off"

    try:
        st = store or get_venue_selection_store()
        venues, dropped = st.active_venues(user_id, connected=connected)
    except Exception as exc:
        log.warning("venue routing: selection unreadable for %s (%s) — single "
                    "venue", user_id, exc)
        return {"mode": mode, "venues": (), "dropped": (), "effective": (),
                "reason": "selection unreadable — single venue"}

    if mode == "off":
        reason = "multi-venue trading is off — single venue"
    elif not venues:
        reason = "no venues selected — single venue"
    elif mode == "shadow":
        reason = (f"SHADOW: would route across {', '.join(venues)} — "
                  "executing single-venue")
    else:
        reason = f"routing across {', '.join(venues)}"

    if mode == "enforce" and not ENFORCE_IMPLEMENTED:
        # Not a silent downgrade: an operator who asked for enforce is told
        # they did not get it. Reporting `shadow` without saying why would let
        # them read the logs as confirmation the feature is live.
        log.warning("MULTI_VENUE_TRADING_MODE=enforce, but no caller routes on "
                    "it yet — running as shadow. Nothing is routed across "
                    "venues.")
        mode = "shadow"
        reason = ("ENFORCE REQUESTED BUT NOT AVAILABLE — order routing is not "
                  "wired yet; running as shadow. " + reason)
    effective = venues if (mode == "enforce" and venues) else ()
    if dropped:
        reason += (f" · selected but not connected: {', '.join(dropped)}")
    return {"mode": mode, "venues": tuple(venues), "dropped": tuple(dropped),
            "effective": tuple(effective), "reason": reason}


# ── which of the selected venues gets THIS order ─────────────────────────

def choose_venue(candidates, need_usd: float) -> tuple:
    """``(venue | None, reason)`` — where one order goes.

    ONE ORDER GOES TO ONE VENUE. Not to all of them: placing the same idea on
    two venues doubles the exposure while every per-venue check still passes,
    which is §2's "two venues each with their own max-5 is ten positions" in
    its purest form. Multi-venue means the book is SPREAD across venues, not
    that each trade is duplicated onto them.

    ``candidates`` is ``[{"venue": str, "free_usd": float | None}, ...]``.

    Three rules, and the first is the one that costs money if it is dropped:

    * A venue whose free margin could NOT BE READ is not a candidate.
      Unreadable is not "has margin" — routing there is betting the order on a
      number nobody read, and the failure mode is an order rejected by the
      exchange at best and a mis-sized one at worst. §7 of the scope says this
      in as many words: an unreadable balance must not read as free margin.
    * A venue without room for the order is not a candidate.
    * Among the rest, the MOST free margin wins — best odds of filling, and it
      spreads the book rather than concentrating it. Ties break alphabetically
      so the same inputs always produce the same venue; a router that picks
      differently on identical state is one nobody can reason about after the
      fact.

    ``(None, reason)`` when nothing qualifies. The caller must REFUSE the
    order. It must not fall back to the default venue — that would place a
    trade somewhere the person did not choose, which is worse than not trading.
    """
    eligible, skipped = [], []
    for c in candidates or []:
        venue = str((c or {}).get("venue") or "").lower().strip()
        if not venue:
            continue
        free = (c or {}).get("free_usd")
        if free is None:
            skipped.append(f"{venue} (margin unreadable)")
            continue
        try:
            f = float(free)
        except (TypeError, ValueError):
            skipped.append(f"{venue} (margin unreadable)")
            continue
        if f != f:                                   # NaN
            skipped.append(f"{venue} (margin unreadable)")
            continue
        if f < float(need_usd):
            skipped.append(f"{venue} (${f:,.2f} < ${float(need_usd):,.2f})")
            continue
        eligible.append((f, venue))
    if not eligible:
        detail = "; ".join(skipped) if skipped else "no venues offered"
        return None, f"no venue can take this order: {detail}"
    eligible.sort(key=lambda t: (-t[0], t[1]))
    best = eligible[0][1]
    note = f" (skipped {'; '.join(skipped)})" if skipped else ""
    return best, f"routing to {best}{note}"
