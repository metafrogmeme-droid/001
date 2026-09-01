"""RC-2026-013 — the operator's token must not stay in the URL.

`DASHBOARD_TOKEN` carries trade-confirm, close and halt authority. It enters
the page the only way it can: the operator pastes

    http://box:8080/#token=SECRET

Reading it from the fragment is fine. LEAVING IT THERE is the finding. A URL
fragment persists in browser history, in the address bar, in a bookmark, in
session restore, and in every screenshot or screen-share of that window — and
for as long as it sits there it is readable by any script that reaches the
page. The page has no XSS today; the fragment hands the token to one that
arrives tomorrow.

TWO FILES, and that is why this scans both. `bot/web/dashboard.html` and
`bot/web/performance_chart.html` are served by separate `FileResponse`
handlers with no shared asset pipeline, so the block is duplicated. The
corollary CLAUDE.md keeps repeating — ask which OTHER surface makes the same
claim — is enforced structurally here: a fix applied to one and not the other
fails this file.

Run under node rather than grepped, following
`tests/test_dashboard_mode_badge_honesty.py`. The property is what the URL
looks like AFTERWARDS, and no pattern match can tell `history.replaceState`
from `location.hash = rest`: both mention the hash, and only one of them
actually removes the entry.
"""

import json
import subprocess
from pathlib import Path

import pytest

_WEB = Path(__file__).resolve().parents[1] / "bot" / "web"
PAGES = [_WEB / "dashboard.html", _WEB / "performance_chart.html"]


def _slice_function(src: str, name: str) -> str:
    """Source of `function <name>(...) {...}` by brace matching."""
    start = src.find(f"function {name}(")
    if start == -1:
        raise AssertionError(
            f"{name}() is not defined. The token read has no seam, so nothing "
            "can drive it and read what the URL looks like afterwards."
        )
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces while slicing {name}()")


def _drive(page: Path, hash_value: str):
    """Run the real _takeTokenFromHash against a stubbed browser.

    The stub records what `history.replaceState` was handed, which is the
    whole question: what is left in the address bar and the history entry.

    `hash_value` is given WITHOUT the leading '#' and the stub adds it, because
    that is what a browser reports. The first version of this helper passed it
    through bare, the shipped `.slice(1)` ate the first real character, and
    every drive returned null — so the two "no token here" cases passed and
    everything else failed. A fixture that models the browser wrongly tests
    nothing, and it fails in the direction that looks like a code bug.
    """
    hash_value = ("#" + hash_value) if hash_value else ""
    fn = _slice_function(page.read_text(encoding="utf-8"), "_takeTokenFromHash")
    script = f"""
const window = {{ location: {{
  hash: {json.dumps(hash_value)}, pathname: '/', search: '' }} }};
let replaced = null, pushed = false;
// A REAL replaceState rewrites the address bar, so location.hash changes too.
// The first stub only recorded the url and left the hash alone, which made
// `remaining_hash` report the token still present on a page that had already
// removed it — the stub failing the code for the stub's own omission.
const history = {{ replaceState: (a, b, url) => {{
  replaced = url;
  const i = String(url).indexOf('#');
  window.location._h = i === -1 ? '' : String(url).slice(i);
}} }};
// If the code assigns location.hash instead, that PUSHES an entry and the
// token-bearing one stays reachable with Back. Catch it as a distinct fact.
Object.defineProperty(window.location, 'hash', {{
  get() {{ return this._h; }},
  set(v) {{ pushed = true; this._h = v; }},
  configurable: true,
}});
window.location._h = {json.dumps(hash_value)};
{fn}
const token = _takeTokenFromHash();
process.stdout.write(JSON.stringify({{
  token, replaced, pushed, remaining_hash: window.location._h }}));
"""
    proc = subprocess.run(["node", "-e", script],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(f"node failed for {page.name}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
class TestTheTokenIsConsumedNotParked:

    def test_the_token_is_read(self, page):
        """Guard the guard: a function that returns null always 'strips'."""
        assert _drive(page, "token=SECRET")["token"] == "SECRET"

    def test_the_url_no_longer_carries_it(self, page):
        out = _drive(page, "token=SECRET")
        assert "SECRET" not in (out["replaced"] or ""), \
            "the token is still in the URL the history entry was replaced with"
        assert "SECRET" not in (out["remaining_hash"] or ""), \
            "the token is still in the live location.hash"

    def test_it_REPLACES_the_history_entry_rather_than_pushing_one(self, page):
        """The subtlety the whole fix turns on.

        `location.hash = rest` looks like a strip and is not: it pushes a NEW
        entry, leaving the token-bearing one behind it and reachable with the
        Back button. Only replaceState removes it. No grep distinguishes the
        two — both mention the hash.
        """
        out = _drive(page, "token=SECRET")
        assert out["replaced"] is not None, \
            "history.replaceState was never called — the old entry survives"
        assert out["pushed"] is False, \
            "location.hash was assigned, which pushes a new history entry"

    def test_other_fragment_params_survive(self, page):
        """A security fix must not silently eat a future view-router's state."""
        out = _drive(page, "view=risk&token=SECRET&tab=2")
        assert out["token"] == "SECRET"
        assert "view=risk" in (out["replaced"] or "")
        assert "tab=2" in (out["replaced"] or "")
        assert "SECRET" not in (out["replaced"] or "")

    def test_a_hash_with_no_token_is_left_completely_alone(self, page):
        """No token, no rewrite. Touching the URL anyway would be a side effect
        nobody asked for, on a page whose hash may later mean something."""
        out = _drive(page, "view=risk")
        assert out["token"] is None
        assert out["replaced"] is None
        assert out["pushed"] is False

    def test_an_empty_hash_is_not_an_error(self, page):
        out = _drive(page, "")
        assert out["token"] is None
        assert out["replaced"] is None


def test_both_pages_actually_have_the_guard():
    """The reason this file exists in the plural.

    Two `FileResponse` handlers, no shared asset pipeline, one duplicated
    block. A fix that lands on the dashboard and not on the chart leaves the
    token in the URL on the page an operator opens second.
    """
    for page in PAGES:
        src = page.read_text(encoding="utf-8")
        assert "_takeTokenFromHash" in src, f"{page.name} still parks the token"
        assert "history.replaceState" in src, f"{page.name} does not replace the entry"
