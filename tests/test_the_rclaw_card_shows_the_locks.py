"""/rclaw shows the time locks as the record writes them, and invents nothing.

The record's locks block (``token/config/rclaw.mainnet.json``) names, for each
vested bucket, its schedule, every unlock, the lock account, the wallet that
claims and Smithii's certificate. ``token/presale/the_record_states_the_locks
.test.mjs`` holds those to the plan and to a snapshot of the chain; this file
holds the card to the record, in three values: a lock not created yet says so,
a field of a shape the record did not promise is *unreadable*, and a
certificate link is printed only when it is Smithii's
(``tests/fixtures/lock_certificate_cases.json``, which the website's /token
page is held to as well).
"""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from bot.formatters import token_card
from bot.formatters.token_card import locks_lines, render_token_card
from bot.token.record import load_record

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests" / "fixtures" / "lock_certificate_cases.json").read_text(encoding="utf-8"))
TELEGRAM_MAX = 4096


def _split_at() -> int:
    """The length at which the bot's ``_send`` splits a reply in two, read from its source.

    A card under Telegram's 4,096 but over this goes out as two messages, so
    this, not 4,096, is what "one message" means. Read by AST rather than
    retyped, so a change to the split point moves this test with it.
    """
    tree = ast.parse((ROOT / "bot" / "skills" / "telegram_handler.py").read_text(encoding="utf-8"))
    sends = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_send"]
    assert len(sends) == 1, f"{len(sends)} _send definitions"
    found = [n.value.value for n in ast.walk(sends[0])
             if isinstance(n, ast.Assign) and [getattr(t, "id", None) for t in n.targets] == ["MAX_LEN"]
             and isinstance(n.value, ast.Constant)]
    assert len(found) == 1, f"{len(found)} MAX_LEN assignments in _send"
    assert 0 < found[0] <= TELEGRAM_MAX
    return found[0]


def _record():
    return copy.deepcopy(load_record())


def _block(lines, bucket):
    """The three lines the card prints for one lock, found by its own heading line."""
    heads = [i for i, line in enumerate(lines) if line.startswith(f"<b>{bucket.replace('&', '&amp;')}</b>: ")]
    assert len(heads) == 1, f"{bucket}: {len(heads)} heading lines"
    return lines[heads[0]:heads[0] + 3]


def test_every_lock_is_printed_with_its_amount_schedule_and_every_unlock():
    record = _record()
    lines = locks_lines(record)
    assert lines[0] == "🔒 <b>Locked allocations</b>"
    for item in record["locks"]["items"]:
        head, unlocks, _ = _block(lines, item["bucket"])
        assert head == (f"<b>{item['bucket'].replace('&', '&amp;')}</b>: "
                        f"{int(item['tokens']):,} RCLAW · {item['schedule']}")
        want = " · ".join(f"{u['at'][:10]} {u['at'][11:16]} {u['percent']}%" for u in item["unlocks"])
        assert unlocks == f"Unlocks (UTC): {want}"
    assert token_card.LOCKS_NOTE in lines
    assert f"Checked against the chain on {record['locks']['verified_at']}." in lines


def test_a_created_lock_links_its_account_and_certificate_and_one_not_created_says_so():
    record = _record()
    lines = locks_lines(record)
    created = [i for i in record["locks"]["items"] if i["vesting_account"] is not None]
    assert len(created) >= 2, "the Community and Team locks exist"
    for item in record["locks"]["items"]:
        where = _block(lines, item["bucket"])[2]
        if item["vesting_account"] is None:
            assert where == "Lock: not created yet"
            continue
        assert where == (
            f'Lock: <a href="https://solscan.io/account/{item["vesting_account"]}">'
            f'<code>{item["vesting_account"]}</code></a> · '
            f'claimed by <code>{item["claim_wallet"]}</code> · '
            f'<a href="{item["certificate_url"]}">certificate</a>')


@pytest.mark.parametrize("url", CASES["accept"])
def test_a_smithii_vesting_certificate_is_printed_as_a_link(url):
    assert token_card.is_certificate_url(url)
    record = _record()
    item = next(i for i in record["locks"]["items"] if i["vesting_account"] is not None)
    item["certificate_url"] = url
    assert f'<a href="{url}">certificate</a>' in _block(locks_lines(record), item["bucket"])[2]


@pytest.mark.parametrize("url", CASES["refuse"])
def test_any_other_certificate_link_is_unreadable_and_not_printed(url):
    assert not token_card.is_certificate_url(url)
    record = _record()
    item = next(i for i in record["locks"]["items"] if i["vesting_account"] is not None)
    item["certificate_url"] = url
    where = _block(locks_lines(record), item["bucket"])[2]
    assert where.endswith(" · unreadable")
    assert "certificate</a>" not in where


def test_no_block_is_unreadable_and_a_null_block_is_not_created_yet():
    record = _record()
    del record["locks"]
    assert locks_lines(record) == ["🔒 <b>Locked allocations</b>: unreadable"]
    record["locks"] = None
    assert locks_lines(record) == ["🔒 <b>Locked allocations</b>: not created yet"]
    record["locks"] = {"items": []}
    assert locks_lines(record) == ["🔒 <b>Locked allocations</b>: unreadable"]


def test_a_field_of_another_shape_is_unreadable_never_printed():
    record = _record()
    item = next(i for i in record["locks"]["items"] if i["vesting_account"] is not None)
    item["vesting_account"] = "not-an-address"
    item["claim_wallet"] = 42
    item["tokens"] = "250,000,000"
    item["unlocks"][0]["at"] = "2026-10-29"
    head, unlocks, where = _block(locks_lines(record), item["bucket"])
    assert head.startswith(f"<b>{item['bucket'].replace('&', '&amp;')}</b>: unreadable · ")
    assert unlocks == "Unlocks (UTC): unreadable"
    assert where.startswith("Lock: unreadable · claimed by unreadable · ")
    assert "not-an-address" not in where


def test_a_missing_field_is_unreadable_not_not_created():
    record = _record()
    item = next(i for i in record["locks"]["items"] if i["vesting_account"] is not None)
    del item["certificate_url"]
    where = _block(locks_lines(record), item["bucket"])[2]
    assert where.endswith(" · unreadable")


@pytest.mark.parametrize("unlocks", [
    [{"at": "2026-10-29T14:00:00Z", "percent": 5}, {"at": "2027-07-29T14:00:30Z", "percent": 20}],
    [{"at": "2026-10-29T14:00:00Z", "percent": 0}],
    [{"at": "2026-10-29T14:00:00Z", "percent": 101}],
    [{"at": "2026-10-29T14:00:00Z", "percent": "5"}],
    [{"at": "2026-10-29T14:00:00Z", "percent": True}],
    [None],
    [],
    "soon",
])
def test_one_unlock_of_another_shape_makes_the_list_unreadable(unlocks):
    record = _record()
    item = record["locks"]["items"][0]
    item["unlocks"] = unlocks
    assert _block(locks_lines(record), item["bucket"])[1] == "Unlocks (UTC): unreadable"


def test_the_whole_card_fits_one_telegram_message():
    # The card is one reply: Telegram refuses a message over 4,096 characters,
    # and the bot splits one over its own, lower, limit into two.
    record = _record()
    assert len(render_token_card(record, operator=True, env_mint=None)) <= _split_at()


def test_the_card_carries_the_locks_section_between_the_presale_and_the_warning():
    record = _record()
    card = render_token_card(record).splitlines()
    section = locks_lines(record)
    start = card.index(section[0])
    assert card[start:start + len(section)] == section
    assert start > next(i for i, line in enumerate(card) if "Presale:" in line)
    assert start < next(i for i, line in enumerate(card) if line.startswith("⚠️ Only this mint"))


def test_a_lock_field_still_null_says_not_created_yet_on_its_own():
    # Held without the record's help: once every lock exists, no item in the
    # record has a null field, and this arm would go untested.
    record = _record()
    item = next(i for i in record["locks"]["items"] if i["vesting_account"] is not None)
    for key in ("vesting_account", "claim_wallet", "certificate_url"):
        one = copy.deepcopy(record)
        target = next(i for i in one["locks"]["items"] if i["bucket"] == item["bucket"])
        target[key] = None
        where = _block(locks_lines(one), item["bucket"])[2]
        assert "not created yet" in where, key
        assert "unreadable" not in where, key
    for i in record["locks"]["items"]:
        i.update(vesting_account=None, claim_wallet=None, certificate_url=None)
    for i in record["locks"]["items"]:
        assert _block(locks_lines(record), i["bucket"])[2] == "Lock: not created yet"
