"""Publish a fair-value board.

The rows are replays, not live quotes. Keys are generated in this
process and dropped; nothing is written except the board JSON when a
path is given.

    python3 scripts/fvb_board.py /tmp/fvb_board.json
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bot.admin_preview import flag_on_for  # noqa: E402
from bot.fvb.board import load_board, save_board  # noqa: E402
from bot.fvb.chains import assert_testnet_chain  # noqa: E402
from bot.fvb.config import FvbConfig  # noqa: E402
from bot.fvb.euler import OP_LIQUIDATE, liquidate_enabled  # noqa: E402
from bot.fvb.packet import SESSION_CODE, public_signal, scale_price  # noqa: E402
from bot.fvb.pricer import Pricer, Reading  # noqa: E402
from bot.fvb.render import card_text  # noqa: E402
from bot.fvb.replay import run_sk_hynix, run_weekends  # noqa: E402
from bot.fvb.sign import (  # noqa: E402
    Committee,
    PacketFields,
    Signer,
    packet_words,
    require_scheme,
    symbol_word,
)


def _row(reading: Reading) -> dict[str, object]:
    return {
        "symbol": reading.symbol,
        "session": reading.session,
        "confirmed": reading.confirmed,
        "forced": reading.forced,
        "fv": reading.fv,
        "band_lo": reading.band_lo,
        "band_hi": reading.band_hi,
        "external": reading.external,
        "last_trade": reading.last_trade,
        "w_ext": reading.w_ext,
        "n_eff_min": reading.n_eff_min,
        "borrow_power": reading.borrow_power,
        "liquidation": reading.liquidation,
        "oi_scale": reading.oi_scale,
        "ts": reading.ts,
        "unit": reading.unit,
        "basis": reading.basis,
    }


def build_rows() -> list[dict[str, object]]:
    pricer = Pricer(FvbConfig())
    sk = run_sk_hynix(pricer)
    weekends = run_weekends(pricer)
    latest = pricer.current_reading(sk[-1].symbol)
    if latest is None:
        raise RuntimeError("replay produced no reading")
    rows = [_row(sk[-1])]
    rows.extend(_row(item) for item in weekends[:9])
    return [public_signal(row) for row in rows]


def signer_roundtrip() -> bool:
    """Sign and check one packet with keys that die with this process."""
    require_scheme("secp256k1")
    require_scheme("ed25519")
    assert_testnet_chain(11155111)
    contract = bytes.fromhex("000000000000000000000000000000000000ffb0")
    signers = [Signer.mint_signer(f"k{i}", "secp256k1") for i in range(5)]
    committee = Committee(signers, threshold=3, chain_id=11155111, verifying_contract=contract)
    public = signers[0].public_key_bytes()
    committee.commit_next_key(signers[0].signer_id, public)
    fv = scale_price(100.0)
    lo = scale_price(99.0)
    hi = scale_price(101.0)
    if fv is None or lo is None or hi is None:
        return False
    fields = PacketFields(
        symbol=symbol_word("EQ"),
        fv=fv,
        band_lo=lo,
        band_hi=hi,
        session=SESSION_CODE["regular"],
        confirmed=True,
        ts=1,
        version=1,
        next_epoch=bytes(32),
    )
    packet_words(fields)
    signed = committee.sign_packet(fields)
    if not committee.verify_packet(fields, signed[:3]):
        return False
    nxt = Signer.mint_signer(signers[0].signer_id, "secp256k1")
    committee.commit_next_key(signers[0].signer_id, nxt.public_key_bytes())
    committee.rotate_signer(signers[0].signer_id, nxt)
    if not liquidate_enabled(OP_LIQUIDATE):
        return False
    return flag_on_for(None, "FVB_ENABLED", store={}) is False


def main(argv: list[str]) -> int:
    rows = build_rows()
    ok = signer_roundtrip()
    if len(argv) > 1:
        path = Path(argv[1])
        doc = save_board(path, rows)
        text = card_text(load_board(path), preview=False)
    else:
        doc = {"rows": rows, "available": True}
        text = card_text(doc, preview=False)
    sys.stdout.write(text + "\n")
    sys.stdout.write(f"rows={len(doc.get('rows', []))} signer_ok={ok}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
