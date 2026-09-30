#!/usr/bin/env python3
"""Score the chat eval.

    python3 scripts/chat_eval.py
        Replay evals/chat/cases.jsonl. No network. This is what CI runs.

    python3 scripts/chat_eval.py --live
        Send the reply rows to the pinned CHAT model and score those replies.
        Needs the endpoint. A failure to reach it exits 2 and names the
        exception class, not the message.

    python3 scripts/chat_eval.py --live --provider runeclaw --model v14-real-14b
        Score that model on the same reply rows without changing the pin.
        Run it once for the pinned chat model and once for v14 before any
        chat-pin change. Route rows are not part of the live pass: they are
        the router, and CI already scores them.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from evals.chat.score import (  # noqa: E402
    PROBE_SYSTEM,
    load_cases,
    probe_user_message,
    score_cases,
    score_live_replies,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score the chat eval cassettes.")
    parser.add_argument("--live", action="store_true",
                        help="call a model for the reply rows; CI does not pass this")
    parser.add_argument("--provider", default="",
                        help="with --live, score this provider instead of the pinned CHAT tier")
    parser.add_argument("--model", default="",
                        help="with --provider, the model id to score")
    return parser


def _live_config(provider_name: str, model: str):
    """The config --live will call. An explicit provider does not write env."""
    from bot.config import CONFIG
    from bot.llm.provider import (
        _PROVIDER_KEY_ENV,
        PROVIDER_CATALOG,
        LLMConfig,
        LLMProvider,
        LLMTier,
        resolve_tier_config,
    )

    if not provider_name:
        primary = LLMConfig(
            provider=LLMProvider(CONFIG.llm.provider) if CONFIG.llm.provider
            else LLMProvider.OPENAI,
            api_key=CONFIG.llm.api_key,
            model=CONFIG.llm.model,
            base_url=CONFIG.llm.base_url,
            timeout_seconds=CONFIG.llm.timeout_seconds,
        )
        return resolve_tier_config(LLMTier.CHAT, primary, is_admin=True)
    try:
        provider = LLMProvider(provider_name.strip().lower())
    except ValueError:
        raise SystemExit(f"unknown provider {provider_name!r}") from None
    catalog = PROVIDER_CATALOG.get(provider, {})
    key_env = _PROVIDER_KEY_ENV.get(provider, "")
    return LLMConfig(
        provider=provider,
        api_key=os.getenv(key_env, "") if key_env else "",
        model=model or catalog.get("default_model", ""),
        base_url=catalog.get("base_url", ""),
    )


async def _ask_all(cases: list[dict], complete) -> dict[str, str]:
    replies: dict[str, str] = {}
    for case in cases:
        if case.get("kind") != "reply":
            continue
        replies[case["id"]] = await complete(PROBE_SYSTEM, probe_user_message(case))
    return replies


async def run_live(cases: list[dict], provider_name: str, model: str) -> int:
    from bot.llm.provider import create_llm_client, llm_complete

    config = _live_config(provider_name, model)
    if not config.is_configured():
        print("CANNOT CHECK: the model is not configured", file=sys.stderr)
        return 2

    async def complete(system: str, user: str) -> str:
        client = create_llm_client(config)
        return await llm_complete(client, config, system, user)

    label = f"{config.provider.value}/{config.model}"
    try:
        replies = await _ask_all(cases, complete)
    except Exception as exc:
        print(f"CANNOT CHECK: {type(exc).__name__} calling {label}", file=sys.stderr)
        return 2
    fails = score_live_replies(cases, replies)
    print(f"live {label}: {len(replies)} reply rows, {len(fails)} failures")
    for line in fails:
        print(line)
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cases = load_cases()
    except ValueError as exc:
        print(f"CANNOT CHECK: {exc}", file=sys.stderr)
        return 2
    if args.live:
        if args.model and not args.provider:
            print("CANNOT CHECK: --model needs --provider", file=sys.stderr)
            return 2
        return asyncio.run(run_live(cases, args.provider, args.model))
    fails = score_cases(cases)
    print(f"scored {len(cases)} cases, {len(fails)} failures")
    for line in fails:
        print(line)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
