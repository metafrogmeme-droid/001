# Changelog

All notable changes to RUNECLAW will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

- Claw Guard: `bot/claw_guard/erc8481.py` writes an ERC-8481 (draft) pre-sign risk report from
  the admin-call check's answer: EIP-712 typed data bound to the exact request, typed findings,
  coverage gaps, no score and no verdict. Off by default (`CLAW_GUARD_ERC8481`), signs only through
  an injected signer, not wired to any route. Checked against the draft's test vectors, including
  the `on-list` set credited to RUNECLAW. See `docs/CLAW_GUARD_ERC8481.md`.
- Deep Audit V6 (`docs/AUDIT_REPORT_V6.md`): 18 findings (5 fixed, 13 documented).
  Fixes: enforced `PatternRecord.may_override_risk` on assignment (was
  construction-only); made the compliance restricted-jurisdiction block
  casing/whitespace-insensitive; fixed a `BacktestValidationGate` re-entrant-lock
  deadlock that hung the test suite; made `MultiUserPortfolio` keys canonical so a
  portfolio is never silently recreated; armed the per-symbol cooldown on
  liquidation. Adds `tests/test_audit_v6_fixes.py`.
- Initial open-source release for Bitget AI Base Camp Hackathon S1.
