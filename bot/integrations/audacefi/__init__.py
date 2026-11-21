"""AudaceFi dry-run publisher: risk-gated SOL ideas as small signals to a loopback gateway.

WHAT IT DOES. When ``AUDACEFI_DRY_RUN_ENABLED`` is set, each fresh call the
engine publishes (`RuneClawEngine._publish_engine_ideas`, so already through
the risk gate and the confidence floor, and not a re-offer) is mapped to a
signal of at most 512 bytes::

    {"expiresAtMs": ..., "id": "...", "sequence": N, "side": "long"|"flat",
     "symbol": "SOL", "targetBps": ...}

saved to an append-only outbox, and only then POSTed to
``http://127.0.0.1:8787/signals``. SOL only. A LONG idea is ``long`` at the
configured allocation; a SHORT idea is ``flat`` at 0. Nothing is ever ``short``.

WHAT IT CANNOT DO, and a test holds each
(``tests/test_audacefi_never_reaches_the_order_path.py``):

* reach the order path -- the executor, the engine and the venue clients are
  not in this package's import closure;
* read a live flag, a key, a secret or a wallet -- it holds none and reads
  none;
* send anywhere but the loopback address, which is a constant here, not a
  setting.

OFF BY DEFAULT. With the flag unset, `schedule_audacefi_dry_run` returns
before it touches a file or a socket. Dry-run is the only mode this package
has; the gateway on the other end decides what a signal does.
"""
