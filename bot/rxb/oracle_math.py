"""Canonical RXB price, in integers.

    P_RXB = min(P_mkt, P_back)
    P_back = min(NAV_issuer, (V_chain + w(delta) * V_att) / S)
    w(delta) = 2 ** (-delta / 72h)

P_mkt is a 30-minute DEX TWAP and only an upper bound. Depth within 2%
under 1_000_000 dollars, or a depth that was not read, drops P_mkt out of
the min. It is not inserted as zero.

Quotes are wad: 1e18 means 1.0 loan token per collateral token. Morpho's
``IOracle.price`` is that wad multiplied by 1e18 (1e36 for a 1:1 quote when
both tokens have 18 decimals). Confirmed 2026-10-23 against morpho-blue
``IOracle.sol``. Signers adjust decimals before they sign; this function
does not guess a token's decimals.

An unreadable input returns None. None is not a price of zero.
"""

from __future__ import annotations

HALF_LIFE_S = 72 * 3600
WAD = 10**18
MORPHO_SCALE = 10**18
DEPTH_MIN_USD = 1_000_000
HOUR_S = 3600

#: floor(1e18 * 2**(-1/2**(k+1))) for k = 0..15. The fractional half-life.
_BITS = (
    707106781186547524,
    840896415253714543,
    917004043204671231,
    957603280698573646,
    978572062087700134,
    989228013193975484,
    994599423483633175,
    997296056085470126,
    998647112890970173,
    999323327502650752,
    999661606496243683,
    999830788931929063,
    999915390886613497,
    999957694548431132,
    999978847050491929,
    999989423469314464,
)


def weight_wad(delta_s: int) -> int:
    """w(delta) in wad. Exact at multiples of 72h. Negative ages raise."""
    if isinstance(delta_s, bool) or not isinstance(delta_s, int):
        raise TypeError("delta must be an int of seconds")
    if delta_s < 0:
        raise ValueError("negative attestation age")
    whole, rem = divmod(delta_s, HALF_LIFE_S)
    if whole >= 64:
        return 0
    w = WAD >> whole
    if rem == 0:
        return w
    q = (rem << 16) // HALF_LIFE_S
    bit = 1 << 15
    for k in range(16):
        if q & bit:
            w = w * _BITS[k] // WAD
        bit >>= 1
    return w


def _nn(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def quote_price(
    *,
    nav: object,
    v_chain: object,
    v_att: object,
    supply: object,
    p_mkt: object,
    include_mkt: object,
    depth_usd: object,
    delta_s: object,
) -> int | None:
    """Wad quote, or None when the quote cannot be read.

    ``depth_usd`` is whole dollars of DEX depth inside 2% of mid, the figure
    the signers attest. None and any reading under ``DEPTH_MIN_USD`` both
    drop P_mkt. A present zero depth is a thin book, not a zero price.
    """
    nav_i = _nn(nav)
    chain_i = _nn(v_chain)
    att_i = _nn(v_att)
    supply_i = _nn(supply)
    if nav_i is None or chain_i is None or att_i is None or supply_i is None:
        return None
    if supply_i == 0 or nav_i == 0:
        return None
    if isinstance(delta_s, bool) or not isinstance(delta_s, int):
        return None
    try:
        w = weight_wad(delta_s)
    except (TypeError, ValueError):
        return None
    backed = chain_i + (att_i * w // WAD)
    per = backed * WAD // supply_i
    p_back = per if per < nav_i else nav_i
    depth_i = _nn(depth_usd)
    liquid = include_mkt is True and depth_i is not None and depth_i >= DEPTH_MIN_USD
    if not liquid:
        return p_back
    mkt_i = _nn(p_mkt)
    if mkt_i is None:
        return None
    return mkt_i if mkt_i < p_back else p_back


def morpho_price(
    wad_quote: int | None,
    loan_decimals: int = 18,
    collateral_decimals: int = 18,
) -> int | None:
    """Scale a wad quote by ``1e36 * 10^(loanDec - collDec)`` for a 1.0 price.

    18-decimal loan and collateral stay at wad * 1e18. A positive quote that
    the decimal gap would round to zero is unreadable, not a zero price.
    """
    if wad_quote is None:
        return None
    if isinstance(wad_quote, bool) or not isinstance(wad_quote, int) or wad_quote < 0:
        return None
    if isinstance(loan_decimals, bool) or not isinstance(loan_decimals, int):
        return None
    if isinstance(collateral_decimals, bool) or not isinstance(collateral_decimals, int):
        return None
    if not 0 <= loan_decimals <= 36 or not 0 <= collateral_decimals <= 36:
        return None
    exp = 18 + loan_decimals - collateral_decimals
    if exp >= 0:
        return int(wad_quote * 10 ** exp)
    div = int(10 ** (-exp))
    scaled = wad_quote // div
    if wad_quote != 0 and scaled == 0:
        return None
    return scaled
