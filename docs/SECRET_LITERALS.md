# Secret literals in the tree

Test fixtures that *look* like real credentials still get flagged by GitHub
secret scanning, and a public repo keeps them in git history even after the
line is deleted. Alerts 1–3 (November 2026) were Telegram bot tokens and a
Google API key in tests. The tokens were revoked. The tree must not grow a
new contiguous literal of those shapes.

## The rule

- Build test secrets at runtime with `tests/fake_secrets.py` or
  `app/test/fake_secrets.js` (`join_secret` / `joinSecret`, or the named
  helpers). Do not write a complete Telegram bot token, Google API key,
  OpenAI/Anthropic/xAI/Groq/Perplexity key, JWT, or PEM private key as one
  string in the tree.
- The official vocabulary table in `bot/utils/secret_shapes.py` (and
  `app/lib/secret_shapes.generated.json`, which is generated from it) is
  the documented exception: each row carries the example the scrub must
  catch.
- If a real-looking secret does land in the tree, **rotate first**. Removing
  the line does not make the historical value safe. Then replace the fixture
  with a runtime-built fake.

## What runs

- `python3 scripts/secret_literal_gate.py` — tree gate used by CI, tests and
  the optional pre-commit hook. It prints rule, path and line, never the
  value.
- CI job **Secret literals (tree gate)** also runs pinned gitleaks 8.28.0
  (checksummed) with `.gitleaks-tree.toml` over the current tree, not
  history.
- gitleaks also skips caches, dependencies, VCS metadata, and binary
  assets (images, fonts, wasm, and so on). That is an unscanned location,
  not an exception for storing a secret.
- The existing **Secret scan (gitleaks)** job is unchanged: it is the
  history scan with `.gitleaks.toml` and the fingerprint baseline.

## Optional pre-commit

```bash
pip install pre-commit
pre-commit install
```

`.pre-commit-config.yaml` is opt-in. CI is the mandatory copy of the gate.
