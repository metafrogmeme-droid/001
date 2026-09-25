"""
Venue abstraction for the live money path.

RUNECLAW's LiveExecutor was written against Bitget USDT-perps: the ccxt
constructor, symbol formats ("BTC/USDT:USDT"), the productType/tradeSide
param dialect, the UTA-vs-classic account split, and the native v3
strategy-order channel were all inlined. This module makes the venue a
first-class object so a second perps venue can plug in WITHOUT touching
Bitget behavior.

Design rules (in order of importance):
  1. ZERO Bitget drift. Every BitgetVenue method reproduces the exact
     params/symbols the executor sent before this module existed —
     including the identity `order_symbol` (Bitget resolves spot-form
     symbols on the swap exchange today; do not "fix" that).
  2. The bot's INTERNAL canonical symbol stays "BASE/USDT" everywhere
     (ideas, risk engine, blacklists, learning). Venues translate only at
     the exchange boundary. normalize_symbol()/display_symbol() already
     strip any quote/settle suffix, so USDC symbols round-trip cleanly.
  3. Per-user executors take the venue the credential store recorded —
     when that venue is in `PER_USER_EXECUTION_VENUES`, the ones whose order
     shapes have been driven. `exchange_credentials` is multi-venue (Bitget
     key/secret/passphrase; Hyperliquid wallet_address + agent_private_key),
     and every venue it stores can be linked and read; only the listed ones
     get an executor. A LiveExecutor constructed without a venue named is
     Bitget.

Selection: VENUE env var ("bitget" default). Hyperliquid is USDC-margined
perps (one-way only, no hedge mode, no UTA), authenticated with a wallet
address plus an AGENT private key. A key whose derived address is that
wallet is the master key and is refused. A well-formed key whose address
cannot be derived (eth-account not installed) is recorded as unconfirmed
and still constructs the client. HIP-3 builder perps keep `dex:COIN`
(`xyz:TSLA`); an unreadable coin falls through to the inherited symbol
map so a scan does not raise, and the prefix is never stripped onto the
main dex. ccxt quirks encoded here:
  - market orders (incl. trigger markets) REQUIRE a price (slippage bound)
  - TP triggers must be sent as takeProfitPrice (triggerPrice == SL)
  - clientOrderId must be a 128-bit hex string ("0x" + 32 hex chars)
  - min notional is $10 per order (ccxt market limits carry it)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Optional

import ccxt.async_support as ccxt

from bot.utils.atomic_write import atomic_write_json

logger = logging.getLogger(__name__)

# HIP-3 builder perps are `dex:COIN` (xyz:TSLA). The dex is a short
# lowercase slug; the coin is the market name. A colon in the coin, an
# empty half, or a character the venue does not use is unreadable — None,
# never a guess that drops the prefix onto the main dex.
_HL_DEX = re.compile(r"^[a-z0-9]{1,16}$")
_HL_COIN = re.compile(r"^[A-Z0-9]{1,20}$")


def hyperliquid_coin(symbol: str) -> Optional[str]:
    """The Hyperliquid coin name, or None when the symbol cannot be read.

    ccxt spells a perp ``BASE/QUOTE:SETTLE``. The settle colon lives on the
    quote side, so a HIP-3 colon lives in the BASE. ``xyz:TSLA/USDT`` and
    ``XYZ:tsla`` both read ``xyz:TSLA``. A main-dex name is the uppercased
    coin. ``xyz:``, ``:TSLA`` and ``xyz:TSLA:EXTRA`` are None.
    """
    if not isinstance(symbol, str):
        return None
    raw = symbol.strip()
    if not raw:
        return None
    base = raw.split("/", 1)[0]
    if ":" in base:
        dex, _, coin = base.partition(":")
        if not dex or not coin or ":" in coin:
            return None
        dex_n = dex.lower()
        coin_n = coin.upper()
        if _HL_DEX.fullmatch(dex_n) is None or _HL_COIN.fullmatch(coin_n) is None:
            return None
        return f"{dex_n}:{coin_n}"
    coin_n = base.upper()
    if _HL_COIN.fullmatch(coin_n) is None:
        return None
    return coin_n


def hyperliquid_key_role(private_key: str, wallet_address: str) -> str:
    """``agent``, ``master``, ``unconfirmed`` or ``rejected``.

    Reuses ``check_signing_key`` so the curve-order check is not copied.
    The address is the only thing compared. No key material is returned.

    ``rejected`` — not a signing key.
    ``unconfirmed`` — well-formed, and no address could be derived (the
    signing library is absent, which is the CI install) or the wallet
    address to compare against is blank.
    ``master`` — the derived address is the wallet. That key can withdraw.
    ``agent`` — the derived address is a different account.
    """
    from bot.web.web3_signer import check_signing_key

    verdict = check_signing_key(private_key)
    if not verdict.get("ok"):
        return "rejected"
    address = verdict.get("address")
    wallet = str(wallet_address or "").strip()
    if not address or not wallet:
        return "unconfirmed"
    if str(address).lower() == wallet.lower():
        return "master"
    return "agent"

def hyperliquid_key_refusal(role: str) -> Optional[str]:
    """The sentence a ``master`` or ``rejected`` key role is refused with, or None.

    One sentence per role, read by the client constructor and by the /connect
    probe, so the key that is refused at the first order is refused in the
    same words at the door. Neither quotes any part of the key.
    """
    if role == "master":
        return ("Hyperliquid trading uses an agent wallet key. This key "
                "controls the master wallet itself, so it was refused. "
                "Nothing was sent.")
    if role == "rejected":
        return ("Hyperliquid refused that private key: it is not a "
                "well-formed signing key. Nothing about the key is "
                "repeated here.")
    return None


# Runtime venue override — set by the admin /venue command, survives
# restarts, and takes precedence over the VENUE env var so switching
# venues never requires editing .env. Removing the file (or /venue clear)
# reverts to the env-configured venue.
_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
VENUE_OVERRIDE_FILE = os.path.join(_STATE_DIR, "venue_override.json")


class Venue:
    """Base venue spec — attribute defaults are documentation only; both
    concrete venues override everything they use."""

    id: str = ""
    display_name: str = ""
    quote: str = "USDT"                 # quote/settle coin of the perp market
    balance_coin: str = "USDT"          # coin whose free balance backs margin
    min_notional_usd: float = 5.0
    supports_hedge_mode: bool = False   # venue can run hedge (two-sided) mode
    supports_native_triggers: bool = False  # Bitget v3 strategy-order channel
    market_order_needs_price: bool = False  # ccxt needs price on market orders

    # ── construction / credentials ────────────────────────────────
    def create_exchange(self, cfg: Any,
                        credentials: Optional[dict] = None) -> ccxt.Exchange:
        raise NotImplementedError

    def missing_credentials_error(self, per_user: bool) -> str:
        raise NotImplementedError

    def has_operator_credentials(self, cfg: Any) -> bool:
        raise NotImplementedError

    # ── symbols ───────────────────────────────────────────────────
    def swap_symbol(self, symbol: str) -> str:
        """Map an internal symbol ("UNI/USDT", "UNI/USDT:USDT", "UNI") to
        this venue's ccxt perp symbol."""
        if f":{self.quote}" in symbol:
            return symbol
        if symbol.endswith(f"/{self.quote}"):
            return f"{symbol}:{self.quote}"
        base = symbol.split("/")[0]
        return f"{base}/{self.quote}:{self.quote}"

    def order_symbol(self, symbol: str) -> str:
        """Symbol to hand ccxt order/position calls. Bitget: IDENTITY —
        the executor historically passes spot-form symbols and Bitget's
        swap-default exchange resolves them; changing that would alter
        live behavior. Non-Bitget venues map to their perp symbol."""
        return symbol

    # ── param dialects ────────────────────────────────────────────
    def futures_params(self, **extra: Any) -> dict:
        """Product-scoping params merged into fetch/order calls."""
        return dict(extra)

    def order_read_params(self) -> dict:
        """Params every ``fetch_order`` on this venue needs before ccxt will
        send it. Empty on most venues; the executor merges it into every
        order read through one seam (``LiveExecutor._fetch_order``)."""
        return {}

    def entry_params(self, margin_mode: str, leverage: int) -> dict:
        """Params for the entry create_order call."""
        return {}

    def post_only_params(self) -> dict:
        return {"postOnly": True}

    def gtc_params(self) -> dict:
        return {"timeInForce": "GTC"}

    def close_params(self, is_uta: Optional[bool]) -> dict:
        """Params for reduceOnly market closes (full and partial)."""
        return {"reduceOnly": True}

    def trigger_params(self, kind: str, trigger_price: float) -> dict:
        """Params for a reduce-only SL/TP trigger-market order.
        kind: "sl" | "tp"."""
        raise NotImplementedError

    def plan_order_queries(self) -> tuple[dict, ...]:
        """One params dict per fetch_open_orders call that lists pending SL/TP
        orders. A caller runs every one and unions the rows; most venues need
        only one query, with no params."""
        return ({},)

    def plan_order_cancel_params(self, order: dict) -> dict:
        """Params for cancel_order on a row the plan queries listed, so the
        cancel reaches the table the order lives in."""
        return {}

    def is_plan_order(self, order: dict) -> bool:
        """Whether an order returned by the plan-order query is an SL/TP
        trigger (venues without a server-side filter need a client check)."""
        return True

    # ── leverage/margin setup (generic ccxt path) ─────────────────
    # Some venues want an explicit set_margin_mode call before leverage;
    # some need extra params on set_leverage (BingX: side=BOTH in one-way).
    margin_mode_call_first: bool = False

    def leverage_params(self, margin_mode: str) -> dict:
        return {"marginMode": margin_mode}

    # ── idempotency ───────────────────────────────────────────────
    def client_oid(self, oid: str) -> str:
        """Venue-legal client order id from an internal idempotency key.
        Deterministic: same input -> same output, so timeout-retry dedup
        still works."""
        return oid

    def order_id_params(self, coid: str) -> dict:
        """Params carrying the client order id on create_order."""
        return {"clientOid": coid, "clientOrderId": coid}

    # ── balance ───────────────────────────────────────────────────
    def balance_fetch_params(self) -> dict:
        return {}


class BitgetVenue(Venue):
    """Bitget USDT-M perpetuals — encodes the executor's historical
    behavior EXACTLY (params, symbols, options). Do not "improve" values
    here without a live A/B: this class is the zero-regression contract."""

    id = "bitget"
    display_name = "Bitget"
    quote = "USDT"
    balance_coin = "USDT"
    min_notional_usd = 5.0
    supports_hedge_mode = True
    supports_native_triggers = True
    market_order_needs_price = False

    def create_exchange(self, cfg: Any,
                        credentials: Optional[dict] = None) -> ccxt.Exchange:
        if credentials:
            api_key = credentials.get("api_key") or ""
            api_secret = credentials.get("api_secret") or ""
            passphrase = credentials.get("passphrase") or ""
        else:
            api_key, api_secret, passphrase = (
                cfg.api_key, cfg.api_secret, cfg.passphrase)
        if not api_key or not api_secret:
            raise RuntimeError(
                self.missing_credentials_error(per_user=bool(credentials)))
        # Bitget REQUIRES a passphrase ("password" credential). Without it ccxt
        # builds the client fine but throws a cryptic `bitget requires "password"
        # credential` on the FIRST private call — which surfaced as "exchange
        # auth FAILED" at startup with unprotected live positions. Fail loud here
        # with an actionable message instead (name the exact missing input).
        if not passphrase:
            if credentials:
                raise RuntimeError(
                    "Your linked Bitget account is missing its passphrase. "
                    "Re-run /connect and include the API passphrase.")
            raise RuntimeError(
                "BITGET_PASSPHRASE is missing — Bitget rejects every request "
                "without the API passphrase. Set it via the admin /setexchange "
                "command (persists across .env wipes), or in .env as "
                "BITGET_PASSPHRASE (the legacy BITGET_API_PASSPHRASE name is "
                "also accepted), then restart.")
        is_futures = cfg.trade_mode == "futures"
        exchange = ccxt.bitget({
            "aiohttp_trust_env": True,  # honor HTTPS_PROXY/CA env (no-op without proxy)
            "apiKey": api_key,
            "secret": api_secret,
            "password": passphrase,
            "timeout": 30000,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap" if is_futures else "spot",
                "uta": True,  # Support Bitget Unified Trading Account
            },
        })
        # Explicit demo-trading activation (PAPTRADING header). The previous
        # constructor "sandbox" key was silently ignored by older ccxt, which
        # made BITGET_SANDBOX dead config; set_sandbox_mode is version-stable
        # and matches the key-validation path in exchange_credentials.
        if cfg.sandbox:
            exchange.set_sandbox_mode(True)
        return exchange

    def missing_credentials_error(self, per_user: bool) -> str:
        if per_user:
            return ("This user has no linked Bitget credentials. Use /connect "
                    "to link an account before trading live.")
        return ("BITGET_API_KEY and BITGET_API_SECRET required for live "
                "trading. Set them in .env and restart.")

    def has_operator_credentials(self, cfg: Any) -> bool:
        return bool(cfg.api_key and cfg.api_secret)

    def futures_params(self, **extra: Any) -> dict:
        p: dict = {"productType": "USDT-FUTURES"}
        p.update(extra)
        return p

    def entry_params(self, margin_mode: str, leverage: int) -> dict:
        return {
            "productType": "USDT-FUTURES",
            "marginMode": margin_mode,
            "leverage": str(leverage),
        }

    def post_only_params(self) -> dict:
        return {"timeInForce": "post_only"}

    def close_params(self, is_uta: Optional[bool]) -> dict:
        # UTA v3 does NOT support tradeSide — reduceOnly is enough there.
        p: dict = {"productType": "USDT-FUTURES", "reduceOnly": True}
        if not is_uta:
            p["tradeSide"] = "close"
        return p

    def trigger_params(self, kind: str, trigger_price: float) -> dict:
        # Classic (non-UTA) ccxt trigger dialect. Always tradeSide=close +
        # reduceOnly so an SL/TP can never open a reverse position.
        return {
            "triggerPrice": trigger_price,
            "triggerType": "last",
            "productType": "USDT-FUTURES",
            "tradeSide": "close",
            "reduceOnly": True,
        }

    # ccxt sends Bitget's order listing to the PLAN endpoint only on `trigger`
    # (or a `planType`). The old `isPlan` key routes nowhere, so it listed the
    # REGULAR pending orders: no resting stop, and any resting limit order.
    # Driven against ccxt 4.5.56 with the transport stubbed. Bitget files a
    # trigger order (the bot's classic stops) under normal_plan and a position
    # TP/SL under profit_loss, and one query reads one plan type.
    _PLAN_TYPES = ("normal_plan", "profit_loss")

    def plan_order_queries(self) -> tuple[dict, ...]:
        return tuple({"productType": "USDT-FUTURES", "trigger": True, "planType": t}
                     for t in self._PLAN_TYPES)

    def plan_order_cancel_params(self, order: dict) -> dict:
        # A plain cancel goes to the regular table, which does not hold a
        # plan order; `trigger` sends it to cancel-plan-order, under the plan
        # type that listed it.
        listed = order.get("_plan_query") or {}
        return {"productType": "USDT-FUTURES", "trigger": True,
                "planType": listed.get("planType", "normal_plan")}

    def balance_fetch_params(self) -> dict:
        return {"type": "swap"}


class HyperliquidVenue(Venue):
    """Hyperliquid USDC-margined perpetual futures (DEX) via ccxt.

    Account topology is simple: one-way positions only, cross or isolated
    margin per symbol, no UTA/classic split, no native strategy-order
    channel (ccxt trigger orders ARE the venue's trigger orders and rest
    correctly, unlike Bitget UTA where they fire immediately — the whole
    reason the v3 path exists is absent here)."""

    id = "hyperliquid"
    display_name = "Hyperliquid"
    quote = "USDC"
    balance_coin = "USDC"
    min_notional_usd = 10.0
    supports_hedge_mode = False
    supports_native_triggers = False
    market_order_needs_price = True

    def create_exchange(self, cfg: Any,
                        credentials: Optional[dict] = None) -> ccxt.Exchange:
        # Per-user credentials (from a user's /connect hyperliquid) take
        # precedence — {wallet_address, agent_private_key}. Falls back to the
        # operator's env-configured wallet when none are supplied (operator path
        # byte-identical).
        if credentials:
            wallet = str(credentials.get("wallet_address", "") or "")
            priv = str(credentials.get("agent_private_key", "") or "")
        else:
            wallet = getattr(cfg, "hyperliquid_wallet_address", "") or ""
            priv = getattr(cfg, "hyperliquid_private_key", "") or ""
        if not wallet or not priv:
            raise RuntimeError(
                self.missing_credentials_error(per_user=bool(credentials)))
        # Refuse before the client exists. A rejected key is not a key; a
        # master key can withdraw. unconfirmed (library absent) and agent
        # both proceed — refusing unconfirmed would make this venue
        # unusable wherever eth-account is not installed.
        role = hyperliquid_key_role(priv, wallet)
        refusal = hyperliquid_key_refusal(role)
        if refusal:
            raise RuntimeError(refusal)
        exchange = ccxt.hyperliquid({
            "aiohttp_trust_env": True,
            "walletAddress": wallet,
            "privateKey": priv,
            "timeout": 30000,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
            },
        })
        # The role is a reading a test (and a later log) can ask. A field
        # written and read by nobody is the thing this check would become.
        exchange.options["runeclaw_key_role"] = role
        if getattr(cfg, "hyperliquid_testnet", False):
            exchange.set_sandbox_mode(True)
        return exchange

    def missing_credentials_error(self, per_user: bool) -> str:
        if per_user:
            return ("Hyperliquid connect needs a wallet_address and an agent "
                    "(API) wallet private key — reconnect with "
                    "/connect hyperliquid <wallet_address> <agent_private_key>.")
        return ("HYPERLIQUID_WALLET_ADDRESS and HYPERLIQUID_PRIVATE_KEY "
                "required for live trading on Hyperliquid. Set them in .env "
                "and restart.")

    def has_operator_credentials(self, cfg: Any) -> bool:
        return bool(getattr(cfg, "hyperliquid_wallet_address", "")
                    and getattr(cfg, "hyperliquid_private_key", ""))

    def swap_symbol(self, symbol: str) -> str:
        """USDC perp form. A readable coin (main-dex or ``dex:COIN``) is
        used; an unreadable one keeps the inherited map so a scan that
        hands a junk symbol still gets a string instead of a raise.
        """
        coin = hyperliquid_coin(symbol)
        if coin is not None:
            return f"{coin}/{self.quote}:{self.quote}"
        return super().swap_symbol(symbol)

    def order_symbol(self, symbol: str) -> str:
        # Internal symbols are USDT-quoted; Hyperliquid perps are USDC.
        return self.swap_symbol(symbol)

    def entry_params(self, margin_mode: str, leverage: int) -> dict:
        # Margin mode + leverage are set per symbol via set_leverage()
        # beforehand; Hyperliquid's order payload carries neither.
        return {}

    def trigger_params(self, kind: str, trigger_price: float) -> dict:
        # ccxt maps triggerPrice -> tpsl "sl" and takeProfitPrice -> "tp";
        # sending a TP as plain triggerPrice would create a WRONG-WAY stop.
        if kind == "tp":
            return {"takeProfitPrice": trigger_price, "reduceOnly": True}
        return {"triggerPrice": trigger_price, "reduceOnly": True}

    def is_plan_order(self, order: dict) -> bool:
        # No server-side isPlan filter — identify trigger orders so plan
        # cleanup never cancels a resting entry limit order.
        if order.get("triggerPrice") or order.get("stopPrice"):
            return True
        info = order.get("info") or {}
        if isinstance(info, dict):
            return bool(info.get("isTrigger") or info.get("triggerPx"))
        return False

    def client_oid(self, oid: str) -> str:
        # Hyperliquid cloid must be a 128-bit hex string. md5 is exactly
        # 128 bits; deterministic so retry dedup still holds. (Not a
        # security context — just an id-width transform.)
        return "0x" + hashlib.md5(oid.encode(), usedforsecurity=False).hexdigest()

    def order_id_params(self, coid: str) -> dict:
        # No Bitget-style "clientOid" — ccxt hyperliquid does not omit
        # unknown params from the exact-schema payload.
        return {"clientOrderId": self.client_oid(coid)}


class ParadexVenue(Venue):
    """Paradex USDC-margined perpetual futures (on-chain DEX, StarkEx L2) via ccxt.

    Non-custodial, wallet-authenticated like Hyperliquid ({wallet_address,
    agent_private_key}). On-chain settlement makes its fills re-derivable — the
    strongest Proof-of-PnL trust tier (onchain_public). ccxt handles the StarkEx
    onboarding from the wallet key.

    Connectable and read-only-checkable here, and NOT an order path: its order
    shapes have never been driven, so no per-user executor is built for it
    (`PER_USER_EXECUTION_VENUES`)."""

    id = "paradex"
    display_name = "Paradex (DEX)"
    quote = "USDC"
    balance_coin = "USDC"
    min_notional_usd = 10.0
    supports_hedge_mode = False
    supports_native_triggers = False
    market_order_needs_price = False

    def create_exchange(self, cfg: Any,
                        credentials: Optional[dict] = None) -> ccxt.Exchange:
        creds = credentials or {}
        wallet = str(creds.get("wallet_address", "") or "")
        priv = str(creds.get("agent_private_key", "") or "")
        if not wallet or not priv:
            raise RuntimeError(self.missing_credentials_error(per_user=bool(credentials)))
        exchange = ccxt.paradex({
            "aiohttp_trust_env": True,
            "walletAddress": wallet,
            "privateKey": priv,
            "timeout": 30000,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        if getattr(cfg, "sandbox", False):
            try:
                exchange.set_sandbox_mode(True)
            except Exception:
                pass
        return exchange

    def missing_credentials_error(self, per_user: bool) -> str:
        return ("Paradex connect needs a wallet_address and an API (agent) wallet "
                "private key — reconnect with /connect paradex <wallet_address> "
                "<agent_private_key>. Never use your main wallet key.")

    def has_operator_credentials(self, cfg: Any) -> bool:
        return False   # per-user connect venue; the operator trades Bitget

    def order_symbol(self, symbol: str) -> str:
        return self.swap_symbol(symbol)

    def trigger_params(self, kind: str, trigger_price: float) -> dict:
        if kind == "tp":
            return {"takeProfitPrice": trigger_price, "reduceOnly": True}
        return {"stopLossPrice": trigger_price, "reduceOnly": True}

    def is_plan_order(self, order: dict) -> bool:
        return _is_trigger_order(order)

    def order_id_params(self, coid: str) -> dict:
        return {"clientOrderId": coid}


def _is_trigger_order(order: dict) -> bool:
    """Client-side trigger/conditional detection for venues whose
    fetch_open_orders has no reliable server-side plan filter — SL/TP
    cleanup must never cancel a resting entry limit order."""
    if order.get("triggerPrice") or order.get("stopPrice") \
            or order.get("stopLossPrice") or order.get("takeProfitPrice"):
        return True
    info = order.get("info") or {}
    if isinstance(info, dict):
        return bool(info.get("triggerPrice") or info.get("stopOrderType")
                    or info.get("stopPrice") or info.get("isTrigger"))
    return False


class BybitVenue(Venue):
    """Bybit USDT linear perpetuals via ccxt.

    Near drop-in: USDT-quoted "X/USDT:USDT" symbols, coin-denominated
    amounts, ~$5 min notional, resting conditional orders. Differences
    encoded here:
      - order_symbol MUST map to the perp form — Bybit loads spot AND
        swap markets, and a bare "BTC/USDT" resolves to SPOT
      - SL/TP via stopLossPrice/takeProfitPrice params (ccxt derives the
        trigger direction; raw triggerPrice would need triggerDirection)
      - one-way position mode assumed (Bybit's default); hedge-mode
        accounts must be switched to one-way before going live
    """

    id = "bybit"
    display_name = "Bybit"
    quote = "USDT"
    balance_coin = "USDT"
    min_notional_usd = 5.0
    supports_hedge_mode = False
    supports_native_triggers = False
    market_order_needs_price = False
    margin_mode_call_first = True

    def create_exchange(self, cfg: Any,
                        credentials: Optional[dict] = None) -> ccxt.Exchange:
        # Per-user {api_key, api_secret} take precedence; fall back to the
        # operator env keys when none supplied (operator path byte-identical).
        if credentials:
            api_key = str(credentials.get("api_key", "") or "")
            api_secret = str(credentials.get("api_secret", "") or "")
        else:
            api_key = getattr(cfg, "bybit_api_key", "") or ""
            api_secret = getattr(cfg, "bybit_api_secret", "") or ""
        if not api_key or not api_secret:
            raise RuntimeError(
                self.missing_credentials_error(per_user=bool(credentials)))
        return ccxt.bybit({
            "aiohttp_trust_env": True,
            "apiKey": api_key,
            "secret": api_secret,
            "timeout": 30000,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
            },
        })

    def missing_credentials_error(self, per_user: bool) -> str:
        if per_user:
            return ("Bybit connect needs an api_key and api_secret — reconnect "
                    "with /connect bybit <api_key> <api_secret>. Account must be "
                    "in ONE-WAY position mode.")
        return ("BYBIT_API_KEY and BYBIT_API_SECRET required for live "
                "trading on Bybit. Set them in .env and restart. Account "
                "must be in ONE-WAY position mode.")

    def has_operator_credentials(self, cfg: Any) -> bool:
        return bool(getattr(cfg, "bybit_api_key", "")
                    and getattr(cfg, "bybit_api_secret", ""))

    def order_symbol(self, symbol: str) -> str:
        # Bybit resolves "BTC/USDT" to the SPOT market — always perp form.
        return self.swap_symbol(symbol)

    def order_read_params(self) -> dict:
        # ccxt 4.5.56 refuses EVERY fetch_order on a unified account (every
        # Bybit account is one now) with ArgumentsRequired, before sending
        # anything, unless the caller says it knows the endpoint answers only
        # the last 500 orders. Driven: without this key, zero requests and a
        # raise; with it, one request to /v5/order/realtime. The bot reads
        # back its own orders seconds to hours after placing them, well inside
        # that window.
        return {"acknowledged": True}

    def leverage_params(self, margin_mode: str) -> dict:
        # v5 set-leverage takes buyLeverage/sellLeverage (ccxt fills them
        # from the leverage argument); marginMode is set separately.
        return {}

    def trigger_params(self, kind: str, trigger_price: float) -> dict:
        if kind == "tp":
            return {"takeProfitPrice": trigger_price, "reduceOnly": True}
        return {"stopLossPrice": trigger_price, "reduceOnly": True}

    def is_plan_order(self, order: dict) -> bool:
        return _is_trigger_order(order)

    def order_id_params(self, coid: str) -> dict:
        # Bybit orderLinkId (<=36 chars) via ccxt's clientOrderId alias;
        # no Bitget-style raw "clientOid" key.
        return {"clientOrderId": coid}


class BingxVenue(Venue):
    """BingX USDT perpetuals via ccxt.

    The small-account venue: $2 min order notional (vs $5 Bitget /
    $10 Hyperliquid), 700+ USDT perps, coin-denominated amounts.
    Quirks encoded here:
      - order_symbol maps to the perp form (spot+swap both listed)
      - set_leverage requires a side param — "BOTH" in one-way mode
      - SL/TP via stopLossPrice/takeProfitPrice params, client-side
        trigger filtering on open-order queries
    """

    id = "bingx"
    display_name = "BingX"
    quote = "USDT"
    balance_coin = "USDT"
    min_notional_usd = 2.0
    supports_hedge_mode = False
    supports_native_triggers = False
    market_order_needs_price = False
    margin_mode_call_first = True

    def create_exchange(self, cfg: Any,
                        credentials: Optional[dict] = None) -> ccxt.Exchange:
        # Per-user {api_key, api_secret} take precedence; fall back to the
        # operator env keys when none supplied (operator path byte-identical).
        if credentials:
            api_key = str(credentials.get("api_key", "") or "")
            api_secret = str(credentials.get("api_secret", "") or "")
        else:
            api_key = getattr(cfg, "bingx_api_key", "") or ""
            api_secret = getattr(cfg, "bingx_api_secret", "") or ""
        if not api_key or not api_secret:
            raise RuntimeError(
                self.missing_credentials_error(per_user=bool(credentials)))
        return ccxt.bingx({
            "aiohttp_trust_env": True,
            "apiKey": api_key,
            "secret": api_secret,
            "timeout": 30000,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
            },
        })

    def missing_credentials_error(self, per_user: bool) -> str:
        if per_user:
            return ("BingX connect needs an api_key and api_secret — reconnect "
                    "with /connect bingx <api_key> <api_secret>. Account must be "
                    "in ONE-WAY position mode.")
        return ("BINGX_API_KEY and BINGX_API_SECRET required for live "
                "trading on BingX. Set them in .env and restart. Account "
                "must be in ONE-WAY position mode.")

    def has_operator_credentials(self, cfg: Any) -> bool:
        return bool(getattr(cfg, "bingx_api_key", "")
                    and getattr(cfg, "bingx_api_secret", ""))

    def order_symbol(self, symbol: str) -> str:
        return self.swap_symbol(symbol)

    def leverage_params(self, margin_mode: str) -> dict:
        # One-way mode: BingX requires side=BOTH on set-leverage.
        return {"marginMode": margin_mode, "side": "BOTH"}

    def trigger_params(self, kind: str, trigger_price: float) -> dict:
        if kind == "tp":
            return {"takeProfitPrice": trigger_price, "reduceOnly": True}
        return {"stopLossPrice": trigger_price, "reduceOnly": True}

    def is_plan_order(self, order: dict) -> bool:
        return _is_trigger_order(order)

    def order_id_params(self, coid: str) -> dict:
        return {"clientOrderId": coid}


class _KeySecretPerpVenue(Venue):
    """Shared base for USDT-linear-perp CEX venues reached via ccxt with plain
    ``apiKey``/``secret`` (optionally ``password``). Uses ccxt UNIFIED trigger
    params (``stopLossPrice``/``takeProfitPrice`` + reduceOnly) and one-way symbol
    mapping — the same shape BingX uses. Concrete venues set ``id``,
    ``display_name``, ``ccxt_id``, ``needs_passphrase`` and any quirks.

    NOT AN ORDER PATH. These adapters are ccxt-native and unit-tested for
    symbol/param shape, and that is all: OKX, Gate and KuCoin count a perp
    order in CONTRACTS where the executor sends a quantity in COINS. Driven
    with ccxt's own request builders on fabricated markets, 3000 DOGE went out
    as sz=3000 on OKX (contract 1000 DOGE: 3,000,000 DOGE), size=3000 on Gate
    (10 DOGE: 30,000) and size=3000 at leverage 1 on KuCoin (100 DOGE:
    300,000); BTC, lot 1, was refused outright. No per-user executor is built
    for them (`PER_USER_EXECUTION_VENUES`, checked where the engine builds
    one): a linked account reads its balance, and no order routes there until
    the adapter converts sizes and has been driven against a real account."""

    ccxt_id: str = ""
    needs_passphrase: bool = False
    quote = "USDT"
    balance_coin = "USDT"
    supports_hedge_mode = False
    supports_native_triggers = False
    market_order_needs_price = False

    def create_exchange(self, cfg: Any,
                        credentials: Optional[dict] = None) -> ccxt.Exchange:
        creds = credentials or {}
        api_key = str(creds.get("api_key", "") or "")
        api_secret = str(creds.get("api_secret", "") or "")
        passphrase = str(creds.get("passphrase", "") or "")
        if not api_key or not api_secret:
            raise RuntimeError(self.missing_credentials_error(per_user=bool(credentials)))
        if self.needs_passphrase and not passphrase:
            raise RuntimeError(
                f"Your linked {self.display_name} account is missing its API "
                f"passphrase. Re-run /connect {self.id} and include it.")
        factory = getattr(ccxt, self.ccxt_id, None)
        if factory is None:                       # pragma: no cover - import guard
            raise RuntimeError(f"ccxt has no exchange {self.ccxt_id!r}")
        opts: dict[str, Any] = {
            "aiohttp_trust_env": True,
            "apiKey": api_key,
            "secret": api_secret,
            "timeout": 30000,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
        if self.needs_passphrase:
            opts["password"] = passphrase
        exchange = factory(opts)
        if getattr(cfg, "sandbox", False):
            try:
                exchange.set_sandbox_mode(True)
            except Exception:
                pass
        return exchange

    def missing_credentials_error(self, per_user: bool) -> str:
        pw = " <passphrase>" if self.needs_passphrase else ""
        return (f"{self.display_name} connect needs an api_key and api_secret"
                f"{' and passphrase' if self.needs_passphrase else ''} — reconnect "
                f"with /connect {self.id} <api_key> <api_secret>{pw}. Account must "
                "be in ONE-WAY position mode.")

    def has_operator_credentials(self, cfg: Any) -> bool:
        return False   # per-user connect venues; the operator trades Bitget

    def order_symbol(self, symbol: str) -> str:
        return self.swap_symbol(symbol)

    def trigger_params(self, kind: str, trigger_price: float) -> dict:
        if kind == "tp":
            return {"takeProfitPrice": trigger_price, "reduceOnly": True}
        return {"stopLossPrice": trigger_price, "reduceOnly": True}

    def is_plan_order(self, order: dict) -> bool:
        return _is_trigger_order(order)

    def order_id_params(self, coid: str) -> dict:
        return {"clientOrderId": coid}


class OkxVenue(_KeySecretPerpVenue):
    """OKX USDT-margined perpetual swaps via ccxt (apiKey/secret/passphrase)."""
    id = "okx"
    display_name = "OKX"
    ccxt_id = "okx"
    needs_passphrase = True
    min_notional_usd = 5.0


class GateVenue(_KeySecretPerpVenue):
    """Gate.io USDT perpetual swaps via ccxt (apiKey/secret)."""
    id = "gate"
    display_name = "Gate.io"
    ccxt_id = "gate"
    needs_passphrase = False
    min_notional_usd = 5.0


class KucoinVenue(_KeySecretPerpVenue):
    """KuCoin Futures USDT perpetual swaps via ccxt (apiKey/secret/passphrase).
    Uses the dedicated ``kucoinfutures`` ccxt id for the perp product."""
    id = "kucoin"
    display_name = "KuCoin Futures"
    ccxt_id = "kucoinfutures"
    needs_passphrase = True
    min_notional_usd = 5.0


_VENUES: dict[str, Venue] = {
    "bitget": BitgetVenue(),
    "hyperliquid": HyperliquidVenue(),
    "bybit": BybitVenue(),
    "bingx": BingxVenue(),
    "okx": OkxVenue(),
    "gate": GateVenue(),
    "kucoin": KucoinVenue(),
    "paradex": ParadexVenue(),
}


def valid_venue_ids() -> list[str]:
    return sorted(_VENUES)


#: The venues a per-user EXECUTOR may be built for: the ones whose entry,
#: stop, close and size unit have been driven against ccxt's own request
#: builders. Every venue in `_VENUES` can be linked and read; only these
#: place orders. An allow-list rather than a flag per class, so a venue added
#: tomorrow is refused until somebody drives it and writes it in here.
PER_USER_EXECUTION_VENUES = frozenset({"bitget", "bybit", "bingx", "hyperliquid"})


def per_user_execution_refusal(venue_id: Optional[str]) -> Optional[str]:
    """None when a per-user executor may trade ``venue_id``; else why not.

    The sentence names the venue and says what the bot does instead. It
    promises no date: the gate lifts when the adapter is driven, and that is
    not a thing this sentence can schedule. It says nothing about what THIS
    message did — the connect card stores keys, a refused confirm places
    nothing — so each caller adds that half.
    """
    vid = str(venue_id or "").strip().lower()
    if vid in PER_USER_EXECUTION_VENUES:
        return None
    v = _VENUES.get(vid)
    label = v.display_name if v is not None else (vid or "that venue")
    reason = (" — it counts perp orders in contracts where this bot sends coins"
              if isinstance(v, _KeySecretPerpVenue) else "")
    return (f"{label} is linked for balances only. Its order path has not been "
            f"driven against a real account{reason}, so this bot places no "
            f"order there.")


def get_venue_override() -> Optional[str]:
    """The persisted runtime venue override, or None. Any unreadable /
    invalid file content is treated as no-override (fail-safe)."""
    try:
        with open(VENUE_OVERRIDE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        vid = str(data.get("venue", "")).strip().lower()
        if vid in _VENUES:
            return vid
        if vid:
            logger.warning("venue_override.json names unknown venue '%s' "
                           "— ignoring", vid)
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 — corrupt file must never trade-block
        logger.warning("venue_override.json unreadable (%s) — ignoring", exc)
    return None


def set_venue_override(venue_id: Optional[str]) -> None:
    """Persist (or clear, with None) the runtime venue override.
    Atomic write so a crash mid-save can't leave a corrupt file."""
    if venue_id is None:
        try:
            os.remove(VENUE_OVERRIDE_FILE)
        except FileNotFoundError:
            pass
        return
    vid = venue_id.strip().lower()
    if vid not in _VENUES:
        raise ValueError(f"unknown venue '{venue_id}' "
                         f"(valid: {', '.join(sorted(_VENUES))})")
    atomic_write_json(VENUE_OVERRIDE_FILE, {"venue": vid}, indent=None)


def get_venue(venue_id: Optional[str] = None) -> Venue:
    """Resolve a venue spec. No id -> runtime override (set by /venue,
    persisted across restarts) > VENUE env (CONFIG.exchange.venue).
    Unknown ids fall back to Bitget with a critical log rather than
    crashing the trading loop."""
    if venue_id is None:
        venue_id = get_venue_override()
    if venue_id is None:
        from bot.config import CONFIG
        venue_id = getattr(CONFIG.exchange, "venue", "bitget")
    v = _VENUES.get((venue_id or "bitget").strip().lower())
    if v is None:
        logger.critical(
            "Unknown VENUE '%s' — falling back to bitget. "
            "Valid venues: %s", venue_id, ", ".join(sorted(_VENUES)))
        return _VENUES["bitget"]
    return v
