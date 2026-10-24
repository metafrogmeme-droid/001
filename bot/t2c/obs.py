"""One t2c_obs row. Missing measurements stay null."""
from __future__ import annotations

COLUMNS = (
    "ts", "lst", "size", "t_secs_api", "queue_onchain",
    "r_borrow", "y", "amm_quote", "route_best", "p",
)


def obs_row(
    ts: object,
    lst: object,
    size: object,
    t_secs_api: object,
    queue_onchain: object,
    r_borrow: object,
    y: object,
    amm_quote: object,
    route_best: object,
    p: object,
) -> dict:
    return {
        "ts": ts,
        "lst": lst,
        "size": size,
        "t_secs_api": t_secs_api,
        "queue_onchain": queue_onchain,
        "r_borrow": r_borrow,
        "y": y,
        "amm_quote": amm_quote,
        "route_best": route_best,
        "p": p,
    }
