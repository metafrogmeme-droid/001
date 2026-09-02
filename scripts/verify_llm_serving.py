#!/usr/bin/env python3
"""Will the local LLM actually answer, and is the bot asking for more than it grants?

THE FAILURE THIS EXISTS FOR
---------------------------

On 2026-09-02 the engine flapped OFFLINE/online for hours. Every layer looked
healthy and each was telling the truth about itself:

  * the auth proxy logged ``POST /v1/chat/completions 200``
  * ``bot.log`` was empty (the bot runs under systemd; journald had the errors)
  * ``/llmstatus`` listed four configured tiers
  * ``curl /api/ps`` answered ``{"models":[]}`` — nothing wrong, just idle

The actual fault was two numbers that no surface compared. Ollama had
``OLLAMA_NUM_PARALLEL:1`` — one serving slot, because a 256K default context
left room for nothing else — while the bot ran
``SCAN_ANALYSIS_CONCURRENCY=12``. Eleven of every twelve analyses sat in a
512-deep queue, each burning ``LLM_TIMEOUT_SEC`` before failing, and the
all-providers-exhausted path scored that as the provider being dead.

Nobody could see it because *the two numbers live on different machines*. The
server knows its slot count and not the client's concurrency; the client knows
its concurrency and not the slot count. This script is the only thing that
holds both at once.

It also catches the trap next door: ``bot/llm/provider.py`` defaults the
RUNECLAW tier's model to ``runeclaw-v6``, and an Ollama store that does not
carry that tag answers 404 — which the fallback chain scores as another
provider failure rather than as a name that does not exist.

FOUR OUTCOMES, BECAUSE "COULD NOT CHECK" IS NOT "PASSED"
--------------------------------------------------------

  0  every check that ran passed, and every check ran
  1  a real mismatch — the bot will fail or queue against this server
  2  usage error: NOTHING WAS CHECKED
  3  at least one check could not be performed. NOT a pass and NOT a failure.

A gate that reads an unreachable server as "fine" is the defect this repo
spends most of its guard tests preventing, so an unreadable check degrades the
exit code and says which one it was.

USAGE
-----

    python3 scripts/verify_llm_serving.py
    python3 scripts/verify_llm_serving.py --base-url http://localhost:11434/v1
    python3 scripts/verify_llm_serving.py --concurrency 4

Environment read (all optional): ``RUNECLAW_LLM_BASE_URL``,
``RUNECLAW_LLM_MODEL``, ``RUNECLAW_LLM_API_KEY``, ``SCAN_ANALYSIS_CONCURRENCY``,
``OLLAMA_SERVER_LOG``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Mirrors bot/config.py's AppConfig.scan_analysis_concurrency default. Pinned by
# tests/test_verify_llm_serving.py so the two cannot drift apart silently — the
# number is the whole point of the concurrency check, and a stale copy of it
# would make this script confidently wrong rather than merely unhelpful.
DEFAULT_SCAN_CONCURRENCY = 12

# Mirrors bot/llm/provider.py's RUNECLAW default_model. Same reason, same test.
DEFAULT_RUNECLAW_MODEL = "runeclaw-v6"

DEFAULT_BASE_URL = "http://localhost:11434/v1"

OK = "OK"
MISMATCH = "MISMATCH"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "N/A"


@dataclass(frozen=True)
class Check:
    """One question, its answer, and — always — how we know.

    ``status`` is deliberately four-valued. ``UNKNOWN`` means the question
    could not be asked; ``NOT_APPLICABLE`` means it was asked and the
    precondition is definitively absent, which is a different thing and only
    ever set where that distinction is real.
    """

    name: str
    status: str
    detail: str


def check_endpoint(served: list[str] | None, base_url: str) -> Check:
    """Did the server answer at all?"""
    if served is None:
        return Check("endpoint", UNKNOWN,
                     f"no answer from {base_url} — is the server running?")
    return Check("endpoint", OK, f"{base_url} answered with {len(served)} model(s)")


def check_model_available(want: str | None, served: list[str] | None) -> Check:
    """Is the model the bot will ask for actually on this server?

    A missing tag is a 404 at request time, and the fallback chain scores a 404
    the same way it scores a dead provider — so this is the difference between
    "the AI is down" and "you typed a name that isn't there".
    """
    if not want:
        return Check("model", UNKNOWN, "no model configured to check")
    if served is None:
        return Check("model", UNKNOWN, f"cannot tell whether {want!r} is served")
    if want in served:
        return Check("model", OK, f"{want!r} is served")
    shown = ", ".join(sorted(served)[:6]) or "(none)"
    return Check("model", MISMATCH,
                 f"{want!r} is NOT served. Available: {shown}"
                 + (" ..." if len(served) > 6 else ""))


def check_concurrency(client_limit: int | None, granted_slots: int | None) -> Check:
    """Does the bot ask for more parallel work than the server will run?

    Over-subscription does not fail fast. Ollama queues (OLLAMA_MAX_QUEUE
    defaults to 512), so the excess requests wait, blow the per-request
    timeout, and arrive at the bot as provider failures. That is why this is a
    MISMATCH and not a warning: the symptom is indistinguishable from an
    outage.
    """
    if client_limit is None:
        return Check("concurrency", UNKNOWN, "client concurrency not known")
    if granted_slots is None:
        return Check(
            "concurrency", UNKNOWN,
            f"client asks for {client_limit}, but the server's granted slot "
            "count could not be read — load a model (run one analysis) and "
            "re-check")
    if client_limit > granted_slots:
        return Check("concurrency", MISMATCH,
                     f"client concurrency {client_limit} exceeds {granted_slots} "
                     f"serving slot(s) — {client_limit - granted_slots} request(s) "
                     "per batch will queue until they time out. Set "
                     f"SCAN_ANALYSIS_CONCURRENCY={granted_slots}")
    return Check("concurrency", OK,
                 f"client concurrency {client_limit} fits {granted_slots} slot(s)")


def check_context(per_slot_ctx: int | None, loaded_ctx: int | None) -> Check:
    """How much context is actually in force for a loaded model?

    Reported rather than judged: there is no right answer without knowing the
    prompt, and Ollama's OpenAI-compatible endpoint truncates an over-long
    prompt silently rather than erroring — so an undersized window degrades
    answers with nothing in any log saying so. Naming the number is the honest
    contribution; picking it is the operator's call.
    """
    if loaded_ctx is None and per_slot_ctx is None:
        return Check("context", UNKNOWN, "no context length could be read")
    if loaded_ctx == 0:
        return Check("context", NOT_APPLICABLE,
                     "no model is loaded, so no context window is in force")
    parts = []
    if loaded_ctx:
        parts.append(f"{loaded_ctx} in force on the loaded model")
    if per_slot_ctx:
        parts.append(f"{per_slot_ctx} per slot at last load")
    return Check("context", OK, "; ".join(parts))


def rollup(checks: list[Check]) -> int:
    """Worst outcome wins, and UNKNOWN is worse than OK."""
    if any(c.status == MISMATCH for c in checks):
        return 1
    if any(c.status == UNKNOWN for c in checks):
        return 3
    return 0


def _get_json(url: str, api_key: str | None, timeout: float = 6.0) -> dict | None:
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
        # A body that is not an object is not a payload we can read. Saying so
        # here keeps every caller's isinstance check from standing alone.
        return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        # Every failure is the same fact here: we did not get an answer. The
        # caller renders that as UNKNOWN, never as an empty model list.
        return None


def fetch_served_models(base_url: str, api_key: str | None) -> list[str] | None:
    payload = _get_json(base_url.rstrip("/") + "/models", api_key)
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    return [str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")]


def fetch_loaded_context(base_url: str, api_key: str | None) -> int | None:
    """Context window of the currently loaded model, via Ollama's /api/ps.

    Returns 0 when the server answers and nothing is loaded — a real, measured
    "no model is resident", which is not the same as being unable to ask.
    """
    root = re.sub(r"/v\d+/?$", "", base_url.rstrip("/"))
    payload = _get_json(root + "/api/ps", api_key)
    if not isinstance(payload, dict):
        return None
    models = payload.get("models")
    if not isinstance(models, list):
        return None
    if not models:
        return 0
    for m in models:
        if isinstance(m, dict) and isinstance(m.get("context_length"), int):
            return int(m["context_length"])
    return None


def _candidate_log_paths() -> list[Path]:
    override = os.getenv("OLLAMA_SERVER_LOG")
    if override:
        return [Path(override)]
    home = Path.home()
    paths = [home / ".ollama" / "logs" / "server.log"]
    local = os.getenv("LOCALAPPDATA")
    if local:
        paths.insert(0, Path(local) / "Ollama" / "server.log")
    return paths


def read_granted_slots(log_text: str | None) -> tuple[int | None, int | None]:
    """Parse the LAST model-load line for what the scheduler actually granted.

    Requested is not granted. ``OLLAMA_NUM_PARALLEL=4`` is a ceiling; the
    scheduler hands out what fits in VRAM, and on a card shared with a training
    run that can be 1. Only the load line knows.

    llama.cpp's ``--ctx-size`` is the TOTAL across slots, so per-slot context is
    that divided by ``--parallel`` — reading the total as the per-request window
    is how a 16K setting looks like 64K.

    Returns ``(slots, per_slot_ctx)``, either of which may be None.
    """
    if not log_text:
        return (None, None)
    last = None
    for line in log_text.splitlines():
        if "starting llama server" in line:
            last = line
    if last is None:
        return (None, None)
    slots = None
    total_ctx = None
    m = re.search(r"--parallel\s+(\d+)", last)
    if m:
        slots = int(m.group(1))
    m = re.search(r"--ctx-size\s+(\d+)", last)
    if m:
        total_ctx = int(m.group(1))
    per_slot = None
    if total_ctx is not None and slots:
        per_slot = total_ctx // slots
    elif total_ctx is not None:
        per_slot = total_ctx
    return (slots, per_slot)


def _read_log() -> str | None:
    for path in _candidate_log_paths():
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None


def run(base_url: str, model: str | None, api_key: str | None,
        concurrency: int | None) -> tuple[list[Check], int]:
    served = fetch_served_models(base_url, api_key)
    loaded_ctx = fetch_loaded_context(base_url, api_key)
    slots, per_slot_ctx = read_granted_slots(_read_log())
    checks = [
        check_endpoint(served, base_url),
        check_model_available(model, served),
        check_concurrency(concurrency, slots),
        check_context(per_slot_ctx, loaded_ctx),
    ]
    return checks, rollup(checks)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url",
                    default=os.getenv("RUNECLAW_LLM_BASE_URL", DEFAULT_BASE_URL))
    ap.add_argument("--model",
                    default=os.getenv("RUNECLAW_LLM_MODEL", DEFAULT_RUNECLAW_MODEL))
    ap.add_argument("--concurrency", type=int, default=None)
    args = ap.parse_args(argv)

    concurrency = args.concurrency
    if concurrency is None:
        raw = os.getenv("SCAN_ANALYSIS_CONCURRENCY")
        try:
            concurrency = int(float(raw)) if raw else DEFAULT_SCAN_CONCURRENCY
        except ValueError:
            concurrency = None

    checks, code = run(args.base_url, args.model,
                       os.getenv("RUNECLAW_LLM_API_KEY") or None, concurrency)

    print("LLM serving check")
    for c in checks:
        print(f"  [{c.status:<8}] {c.name:<12} {c.detail}")
    verdict = {0: "OK — every check ran and passed",
               1: "MISMATCH — the bot will fail or queue against this server",
               3: "COULD NOT CHECK — this is not a pass"}[code]
    print(verdict)
    return code


if __name__ == "__main__":
    sys.exit(main())
