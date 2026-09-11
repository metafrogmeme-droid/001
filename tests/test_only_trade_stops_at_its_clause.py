"""An operator's allow-list must end where their sentence does.

`compile_nl` parsed "only trade X" with

    only\\s+(?:trade\\s+)?([a-z0-9,\\s/and]+?)(?:\\.|,\\s*(?:never|keep|max|min|and stop)|$)

and that had two ways to get a restriction wrong. The capture class held `\\s`,
which matches NEWLINES, and the negation terminator required a preceding COMMA.

**WIDENED.** "only trade btc\\nnever trade eth" ran the capture to end-of-string
and produced `['BTC', 'ETH', 'NEVER', 'TRADE']` — the operator's own negation
granting the symbol they had just forbidden, plus two English words as tickers.
Nothing downstream catches it: `compile_policy` clamps NUMERIC rules only, so
they "can only tighten", and passes symbol lists through `_base_symbol`
untouched; `evaluate_policy` then enforces the list with `if asset not in val`.

**VANISHED**, which is the quieter half. "only trade btc\\nmax 2% per trade"
matched NOTHING — `%` is outside the capture class, so no terminator was
reachable — and the allow-list was silently ABSENT rather than wrong. A
restriction that disappears leaves no junk behind for anyone to notice, and the
operator is told nothing. Same for the canonical paste `BTC/USDT:USDT`, whose
`:` the class also lacked, on the exact form this product prints everywhere.

Reachable on three live surfaces: `bot/skills/guardian_commands.py` and, in
`bot/web/user_gateway.py`, `_compile_policy_preview` and
`_compile_and_bind_policy` — the latter documented as "The SOLE web path that
writes a policy", with `_POLICY_TEXT_MAX = 600` permitting multi-line text.

Found by the corollary sweep on PR #330: a capture whose positive character
class can eat newlines. There it was a test scanner; here it is a guardian.
"""
from __future__ import annotations

import pytest

from bot.guardian import intent_policy as ip


def _allowed(text: str):
    rules = ip.compile_nl(text)["rules"]
    return next((r["value"] for r in rules if r.get("type") == "allowed_symbols"), None)


# ── the widened case: a negation must never join the allow-list ─────────

@pytest.mark.parametrize("text", [
    "only trade btc, never trade eth",     # the comma the old pattern needed
    "only trade btc\nnever trade eth",     # a newline instead
    "only trade btc never trade eth",      # no punctuation at all
    "only trade btc. never trade eth",
    "only trade btc; never trade eth",
    "only trade btc\nnever eth",
])
def test_a_forbidden_symbol_never_joins_the_allow_list(text):
    assert _allowed(text) == ["BTC"], (
        "the operator forbade it in the same breath and the policy allows it")


# ── ambiguity abstains, and says so ─────────────────────────────────────

@pytest.mark.parametrize("text", [
    "only trade btc not eth",
    "only trade btc, not eth",
    "only trade btc except eth",
    "only trade btc excluding eth",
    "only trade btc without eth",
    "only trade btc but not eth",
    "only trade btc dont trade eth",
    "only trade btc unless eth",
])
def test_an_ambiguous_clause_sets_no_symbol_restriction(text):
    """ENUMERATING NEGATIONS AS TERMINATORS DOES NOT GENERALISE, and the first
    version of this fix tried: it handled "never" and still gave ETH away to
    "not", "except", "without" and "but not". Each miss is a silent widening of
    a whitelist on a guardian surface — an arms race against English dressed up
    as a parser.

    So the rule is not "recognise every negation". It is that a clause this
    parser cannot fully account for yields NO rule."""
    assert _allowed(text) is None, "an ambiguous sentence produced a confident allow-list"


@pytest.mark.parametrize("text", [
    "only trade btc not eth",
    "only trade btc except eth",
    "only trade btc but not eth",
])
def test_the_abstention_is_reported_not_silent(text):
    """Guard, not omit. A restriction that quietly does not exist is the other
    half of the same defect — the operator must be told to put the exclusion in
    its own sentence."""
    notes = [m for m in ip.compile_nl(text)["matched"] if m.startswith("note:")]
    assert notes, "the clause was dropped with nothing said"
    assert "ambiguous" in notes[0]


def test_the_abstention_note_names_the_word_that_caused_it():
    notes = [m for m in ip.compile_nl("only trade btc except eth")["matched"]
             if m.startswith("note:")]
    assert "except" in notes[0]


def test_abstaining_does_not_cost_the_other_rules_in_the_sentence():
    """No symbol rule must not mean no policy: the caps the operator also asked
    for are unaffected, and the missing restriction is visible by its absence
    from the card they approve."""
    out = ip.compile_nl("only trade btc not eth, max 2% per trade")
    types = {r["type"] for r in out["rules"]}
    assert "allowed_symbols" not in types
    assert "max_position_pct" in types


@pytest.mark.parametrize("text", [
    "only trade btc\nnever trade eth",
    "only trade btc never trade eth",
    "only trade btc\nno shorts",
])
def test_the_grammars_own_words_are_not_tickers(text):
    """A 2-6 character length filter passes `never`, `trade`, `no` and `shorts`
    happily. They are not a guess about English — they are the tokens this
    parser's own phrasings are built from."""
    got = _allowed(text) or []
    for word in ("NEVER", "TRADE", "NO", "SHORTS", "AND", "OR"):
        assert word not in got, f"{word} was recorded as a tradable symbol"


# ── the vanished case: a restriction must not silently not exist ────────

@pytest.mark.parametrize("text,want", [
    ("only trade btc\nmax 2% per trade", ["BTC"]),
    ("only trade sol\nstop if down 8% this week", ["SOL"]),
    ("only trade btc\nmin confidence 70%", ["BTC"]),
])
def test_a_following_clause_does_not_erase_the_allow_list(text, want):
    """`%` is outside the capture class, so no terminator was reachable and the
    whole match failed — the allow-list was absent, not wrong. Absence is the
    one a reader cannot see."""
    assert _allowed(text) == want


@pytest.mark.parametrize("text,want", [
    ("only trade btc/usdt:usdt", ["BTC"]),
    ("only trade btc/usdt:usdt, eth/usdt:usdt", ["BTC", "ETH"]),
    ("only trade btc/usdt and eth/usdt", ["BTC", "ETH"]),
])
def test_the_symbol_form_this_product_prints_is_accepted(text, want):
    """`BTC/USDT:USDT` is what every card in this product shows, and
    `_base_symbol` already reduces it. The class lacked `:`, so pasting the
    symbol exactly as displayed produced no allow-list at all."""
    assert _allowed(text) == want


def test_a_pair_is_not_split_into_its_quote_currency():
    """A first draft of the fix put `/` in the separator and produced
    `['BTC','ETH','USDT']` — quietly allowing the quote currency. `/` belongs in
    the capture class, not the split; `_base_symbol` does the reducing."""
    assert "USDT" not in (_allowed("only trade btc/usdt and eth/usdt") or [])


# ── the ordinary readings still read ────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("only trade btc", ["BTC"]),
    ("only trade btc and eth", ["BTC", "ETH"]),
    ("only trade btc or eth", ["BTC", "ETH"]),
    ("only trade btc, eth, sol", ["BTC", "ETH", "SOL"]),
    ("only trade doge, shib and pepe", ["DOGE", "PEPE", "SHIB"]),
    ("only trade btc, max 2% per trade", ["BTC"]),
])
def test_a_genuine_allow_list_is_still_read(text, want):
    assert _allowed(text) == want


def test_a_following_clause_is_still_parsed_as_its_own_rule():
    """Ending the allow-list at the newline must not cost the rule after it."""
    types = {r["type"] for r in ip.compile_nl("only trade btc\nmax 2% per trade")["rules"]}
    assert "allowed_symbols" in types
    assert "max_position_pct" in types


def test_only_majors_still_takes_the_other_branch():
    assert set(_allowed("only majors") or []) == set(ip._MAJORS)


# ── the whole way through to what gets enforced ─────────────────────────

def test_the_bound_policy_refuses_the_symbol_the_operator_forbade():
    """The parse is only half the claim. `compile_policy` does not validate
    symbol lists against any universe — it clamps numeric rules only — so
    whatever the parse produces is what `evaluate_policy` enforces."""
    parsed = ip.compile_nl("only trade btc\nnever trade eth")
    policy = ip.compile_policy({"mode": "enforce", "rules": parsed["rules"]}, {})
    verdict = ip.evaluate_policy(policy, {"asset": "ETH/USDT:USDT"})
    assert verdict["violations"], "ETH was allowed by a policy that forbade it"


def test_the_bound_policy_still_admits_what_was_allowed():
    parsed = ip.compile_nl("only trade btc\nnever trade eth")
    policy = ip.compile_policy({"mode": "enforce", "rules": parsed["rules"]}, {})
    verdict = ip.evaluate_policy(policy, {"asset": "BTC/USDT:USDT"})
    assert not verdict["violations"], verdict


# ── a separator must be a word, not a substring ─────────────────────────

@pytest.mark.parametrize("text,want", [
    ("only trade sand", ["SAND"]),
    ("only trade band", ["BAND"]),
    ("only trade sand and rndr", ["RNDR", "SAND"]),
    ("only trade rndr and sand", ["RNDR", "SAND"]),
])
def test_a_symbol_containing_and_survives_the_split(text, want):
    """The split was `[,\\s]+|and`, with no word boundary, so it cut INSIDE any
    ticker containing those letters. SAND and BAND are real tokens: "only trade
    sand" split to ['s', ''], both dropped by the 2-6 length filter, and the
    allow-list did not exist — no restriction at all, from a sentence that asked
    for one. "only trade sand and rndr" kept RNDR and lost SAND, so the operator
    who named two symbols was restricted to one."""
    assert _allowed(text) == want


# ── the grammar's own glue is not a ticker ──────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("only trade btc and trade eth", ["BTC", "ETH"]),
    ("only the btc", ["BTC"]),
    ("only trade btc and the eth", ["BTC", "ETH"]),
    ("only trades btc", ["BTC"]),
    ("only all btc", ["BTC"]),
])
def test_the_phrasings_own_words_are_dropped_not_recorded(text, want):
    """`trade`, `trades`, `the` and `all` are 3-6 alphanumerics, so the length
    filter passes them happily. Without `_NOT_A_SYMBOL` each of these records a
    ticker named after a word in the operator's own sentence.

    Its first mutation round SURVIVED, and the reason was that no test reached
    it — "and"/"or" are separators and the leading "trade" is consumed by the
    pattern, so every case I had written was already covered by something else.
    A filter with no reaching test is indistinguishable from a dead one."""
    assert _allowed(text) == want
