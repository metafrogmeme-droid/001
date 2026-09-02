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
    # EVERY reply in each ending, not the first one within 900 characters.
    #
    # The 900 was a magic window, and a comment added above the return pushed
    # `_chat_ret` past it — the test failed with "substring not found" on code
    # that was perfectly safe. Worse, when the all-failed ending later grew a
    # SECOND reply (empty completions are not the same fact as unreachable
    # providers), only the first would have been checked, and F-15 is a
    # credential-leak rule that has to hold for all of them.
    #
    # The region now runs from the marker to the next audit( after it — the
    # same landmark the old code used to stop at, applied to the whole span.
    for marker in ("All chat LLM providers failed", "Chat deadline hit after"):
        idx = SRC.index(marker)
        after = SRC[idx:]
        # Past this ending's own audit line, then up to the next one.
        _own = after.index("\n")
        _next_audit = after.find("audit(", _own)
        region = after[:_next_audit if _next_audit != -1 else 1200]
        rets = [i for i in range(len(region)) if region.startswith("_chat_ret", i)]
        assert rets, f"no user reply found for the {marker!r} ending"
        for start in rets:
            ret_block = region[start:start + 700]
            assert "last_error" not in ret_block, (
                f"raw provider error reaches a user reply after {marker!r} (F-15)")
            assert "{error_str" not in ret_block and "str(e)" not in ret_block
    # Each ending still returns its OWN generic, safe message — and they must
    # not be the same message, because they are not the same fact. THREE facts
    # now: providers unreachable, providers reachable and returning nothing,
    # and the budget running out before enough of them were asked.
    def _ending(marker: str) -> str:
        after = SRC[SRC.index(marker):]
        _next = after.find("audit(", after.index("\n"))
        return after[:_next if _next != -1 else 1200]

    all_failed = _ending("All chat LLM providers failed")
    deadline = _ending("Chat deadline hit after")
    assert "temporarily" in all_failed or "trouble thinking" in all_failed, (
        "the unreachable ending lost its message")
    assert "answered but returned nothing" in all_failed, (
        "an empty completion is not an unreachable provider: every provider "
        "returned HTTP 200 and nothing, and the reply blamed availability")
    assert "stopped waiting" in deadline, (
        "the deadline ending must name TIME as the cause; reporting providers "
        "that were never asked as unavailable is a confident negative")
    assert "unavailable" not in deadline[deadline.index("_chat_ret"):][:400]


def test_f15_detail_still_logged_for_operators():
    # The raw last_error must still be audited (operators keep the diagnostic).
    assert re.search(r'audit\([^)]*All chat LLM providers failed[^)]*last_error',
                     SRC.replace("\n", " ")) or \
        'f"All chat LLM providers failed. Last: {last_error}"' in SRC
