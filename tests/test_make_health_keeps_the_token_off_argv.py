"""`make health` must not put DASHBOARD_TOKEN on curl's command line.

The token authorises trade confirm, close and halt. A header built with
command substitution is visible to anyone who can read the process list.
curl reads the header from stdin (`--header @-`); awk reads `.env`.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _health_recipe() -> str:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    start = text.index("health:")
    rest = text[start:]
    # The next target begins at a line that is not a recipe line.
    lines = rest.splitlines()
    out = [lines[0]]
    for line in lines[1:]:
        if line.startswith("\t") or line.startswith("health:"):
            out.append(line)
            continue
        if line.strip() == "" or line.startswith("#") or line.startswith(".PHONY"):
            continue
        break
    return "\n".join(out)


def test_the_health_recipe_feeds_the_header_on_stdin():
    recipe = _health_recipe()
    assert "curl" in recipe
    assert "--header @-" in recipe
    assert "DASHBOARD_TOKEN" in recipe
    assert "Bearer $$(" not in recipe
    assert 'Authorization: Bearer $$(grep' not in recipe
