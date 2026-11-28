# Changelog

All notable changes to RUNECLAW will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

- Claw Guard: `bot/claw_guard/admin_calls.py` finds upgrade and admin-key calls (proxy upgrades,
  admin and owner changes, multisig signer changes, delegatecall, EIP-7702 delegations),
  including inside multicall and batch calls, and blocks them unless the user's own rule
  allows them. A library only: not wired to the order path or any route.
  See `docs/CLAW_GUARD_ADMIN_CALLS.md`.
- Deep Audit V6 (`docs/AUDIT_REPORT_V6.md`): 18 findings (5 fixed, 13 documented).
  Fixes: enforced `PatternRecord.may_override_risk` on assignment (was
  construction-only); made the compliance restricted-jurisdiction block
  casing/whitespace-insensitive; fixed a `BacktestValidationGate` re-entrant-lock
  deadlock that hung the test suite; made `MultiUserPortfolio` keys canonical so a
  portfolio is never silently recreated; armed the per-symbol cooldown on
  liquidation. Adds `tests/test_audit_v6_fixes.py`.
- Initial open-source release for Bitget AI Base Camp Hackathon S1.
