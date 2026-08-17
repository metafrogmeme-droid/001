"""§4 compliance/secret-safety fixes surfaced by the readiness audit.

1. /classpf reads the OPERATOR's shared live-executor book and prints per-class
   dollar PnL. Without an auth guard, any Telegram caller (even unlinked) could
   read one account's dollars — a §4 cross-user dollar-disclosure. It must carry
   an @guard so the F-2 allowlist gates it like every sibling portfolio view.

2. F-15: when every chat LLM provider fails, the user-facing reply must NOT
   contain the raw provider exception (last_error / str(e)) — that string can
   embed a credential-bearing URL or an upstream body echoing an API key. The
   detail belongs in the audit log only.

Source-asserted: wiring the full TelegramHandler is heavy and unnecessary — the
regression we must lock is exactly the presence of the guard and the absence of
the raw error in the user-facing branch.
"""

import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent
       / "bot/skills/telegram_handler.py").read_text(encoding="utf-8")


def test_classpf_is_auth_guarded():
    # The @guard decorator must sit directly above the _cmd_classpf def.
    m = re.search(
        r'@guard\(\s*["\'][a-z_]+["\']\s*\)\s*\n\s*async def _cmd_classpf\b', SRC)
    assert m, "_cmd_classpf must carry an @guard(...) decorator (was ungated → §4 leak)"


def test_classpf_still_reads_the_live_book():
    # Guard added, behaviour otherwise unchanged — it still surfaces the class PnL.
    body = SRC[SRC.index("async def _cmd_classpf"):]
    body = body[:body.index("async def ", 10)]
    assert "closed_positions" in body


def test_f15_no_chat_failure_reply_leaks_the_raw_error():
    """F-15 across BOTH endings of the chat chain.

    This used to anchor on the comment "All providers failed" and check the one
    return block after it. On 2026-08-17 the chain gained a wall-clock deadline
    and therefore a SECOND ending — running out of budget is not the same fact
    as every provider failing — and the old anchor stopped existing, which is
    how the split was noticed here rather than in production.
    #
    Anchoring on a comment was the weakness: it pinned the prose, not the
    property. Both audit calls are the stable landmarks, and the rule is the
    same for each — last_error carries a credential-bearing URL or a 4xx body
    echoing a key, so it goes to the log and never to the user.
    """
    for marker in ("All chat LLM providers failed", "Chat deadline hit after"):
        idx = SRC.index(marker)
        block = SRC[idx:idx + 900]
        ret_start = block.index("_chat_ret")
        ret_block = block[ret_start:]
        # Stop at the NEXT audit(, or the window runs into the following
        # ending's log line — which legitimately DOES interpolate last_error,
        # and the test would fail on correct code. (It did, on the first run.)
        _next_audit = ret_block.find("audit(")
        ret_block = ret_block[:_next_audit if _next_audit != -1 else 400]
        assert "last_error" not in ret_block, (
            f"raw provider error reaches the user reply after {marker!r} (F-15)")
        assert "{error_str" not in ret_block and "str(e)" not in ret_block
    # Each ending still returns its OWN generic, safe message — and they must
    # not be the same message, because they are not the same fact.
    all_failed = SRC[SRC.index("All chat LLM providers failed"):][:900]
    deadline = SRC[SRC.index("Chat deadline hit after"):][:900]
    assert "temporarily" in all_failed or "trouble thinking" in all_failed
    assert "stopped waiting" in deadline, (
        "the deadline ending must name TIME as the cause; reporting providers "
        "that were never asked as unavailable is a confident negative")
    assert "unavailable" not in deadline[deadline.index("_chat_ret"):][:400]


def test_f15_detail_still_logged_for_operators():
    # The raw last_error must still be audited (operators keep the diagnostic).
    assert re.search(r'audit\([^)]*All chat LLM providers failed[^)]*last_error',
                     SRC.replace("\n", " ")) or \
        'f"All chat LLM providers failed. Last: {last_error}"' in SRC
