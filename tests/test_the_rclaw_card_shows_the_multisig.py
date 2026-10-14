"""/rclaw shows the treasury multisig as the record writes it, and invents nothing.

The record's multisig block (``token/config/rclaw.mainnet.json``) names the
vault that holds the treasury, the partnerships bucket and the rest of the
reserve, how many members must approve, the time lock, and what the vault
holds. ``token/presale/the_record_states_the_multisig.test.mjs`` holds those
to a snapshot of the chain; this file holds the card to the record, in three
values: no block is *unreadable*, a ``null`` block is *not created yet*, and a
field of a shape the record did not promise is *unreadable* on its own line.
"""
from __future__ import annotations

import copy

import pytest

from bot.formatters import token_card
from bot.formatters.token_card import multisig_lines, render_token_card
from bot.token.record import load_record


def _record():
    return copy.deepcopy(load_record())


def _line(lines, prefix):
    hits = [line for line in lines if line.startswith(prefix)]
    assert len(hits) == 1, f"{len(hits)} lines start with {prefix!r}"
    return hits[0]


def test_the_card_prints_the_record_s_multisig_field_by_field():
    record = _record()
    ms = record["multisig"]
    lines = multisig_lines(record)
    assert lines[0] == "🏦 <b>Treasury multisig</b>"
    buckets = " · ".join(f"{b['bucket'].replace('&', '&amp;')} {int(b['tokens']):,}" for b in ms["buckets"])
    assert _line(lines, "Holds: ") == f"Holds: {int(ms['holds_tokens']):,} RCLAW ({buckets})"
    hours = ms["time_lock_seconds"] // 3600
    assert ms["time_lock_seconds"] == hours * 3600, "the record's time lock is whole hours"
    assert _line(lines, "Approvals: ") == (
        f"Approvals: {ms['threshold']} of {len(ms['members'])} members · time lock: {hours} hours")
    assert _line(lines, "Vault: ") == (
        f'Vault: <a href="https://solscan.io/account/{ms["vault"]}"><code>{ms["vault"]}</code></a>')
    assert token_card.MULTISIG_NOTE in lines
    assert lines[-1] == f"Checked against the chain on {ms['verified_at']}."
    # The members' own wallets are the record's, for the verifier; the card names only the vault.
    card = "\n".join(lines)
    assert not any(m in card for m in ms["members"])


def test_the_card_carries_the_multisig_between_the_locks_and_the_warning():
    record = _record()
    card = render_token_card(record).splitlines()
    section = multisig_lines(record)
    start = card.index(section[0])
    assert card[start:start + len(section)] == section
    assert start > card.index(token_card.locks_lines(record)[0])
    assert start < next(i for i, line in enumerate(card) if line.startswith("⚠️ Only this mint"))


def test_three_values_for_the_block():
    none = _record()
    del none["multisig"]
    assert multisig_lines(none) == ["🏦 <b>Treasury multisig</b>: unreadable"]
    nul = _record()
    nul["multisig"] = None
    assert multisig_lines(nul) == ["🏦 <b>Treasury multisig</b>: not created yet"]
    odd = _record()
    odd["multisig"] = ["not", "a", "block"]
    assert multisig_lines(odd) == ["🏦 <b>Treasury multisig</b>: unreadable"]


@pytest.mark.parametrize("field,value,prefix", [
    ("threshold", 4, "Approvals: unreadable"),
    ("threshold", 0, "Approvals: unreadable"),
    ("threshold", True, "Approvals: unreadable"),
    ("members", ["not-an-address", "x"], "Approvals: unreadable"),
    ("members", "3 members", "Approvals: unreadable"),
    ("holds_tokens", "309,997,298", "Holds: unreadable"),
    ("holds_tokens", 309997298, "Holds: unreadable"),
    ("buckets", [], "Holds: unreadable"),
    ("buckets", [{"bucket": "Treasury / DAO", "tokens": "200,000,000"}], "Holds: unreadable"),
    ("buckets", [{"bucket": "", "tokens": "1"}], "Holds: unreadable"),
    ("vault", "not-an-address", "Vault: unreadable"),
    ("vault", None, "Vault: not created yet"),
])
def test_a_field_of_another_shape_is_unreadable_on_its_own_line(field, value, prefix):
    record = _record()
    record["multisig"][field] = value
    lines = multisig_lines(record)
    assert any(line.startswith(prefix) for line in lines), lines
    # and only that line: the others still print what the record holds
    assert "🏦 <b>Treasury multisig</b>" == lines[0]
    assert token_card.MULTISIG_NOTE in lines


def test_a_missing_field_is_unreadable_never_blank():
    for field, prefix in (("vault", "Vault: unreadable"), ("buckets", "Holds: unreadable"),
                          ("threshold", "Approvals: unreadable")):
        record = _record()
        del record["multisig"][field]
        assert any(line.startswith(prefix) for line in multisig_lines(record)), field


@pytest.mark.parametrize("seconds,text", [
    (172800, "48 hours"),
    (5400, "5400 seconds"),
    (0, "none: an approved transfer executes at once"),
    (-1, "unreadable"),
    ("48h", "unreadable"),
    (None, "unreadable"),
])
def test_the_time_lock_in_three_values(seconds, text):
    record = _record()
    record["multisig"]["time_lock_seconds"] = seconds
    assert _line(multisig_lines(record), "Approvals: ").endswith(f"time lock: {text}")
