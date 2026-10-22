"""The GitHub page states the token record, and only the token record.

The repository's README is the first page many people see, and a forged
mint costs money. Both READMEs (English and 繁體中文) carry a $RCLAW section
between ``rclaw-record`` markers: the mint, the three locks, the treasury
multisig and the audit of the program that holds the locks. Each is a second
copy of the record as the website publishes it
(``app/content/rclaw.mainnet.json``, written from
``token/config/rclaw.mainnet.json`` by ``app/scripts/sync_content.js``),
which the website's /token page and the bot's /rclaw card print and which the
token tests hold to the chain, so this file holds every copy here to that
record: a changed address, link, date or figure in the record that is not
carried into both READMEs, or an address in the section that the record does
not name, fails here.

NO PRESALE. The owner took the presale off the bot and the website on
22 October 2026, and the GitHub page with them: the section names no sale,
no sale link and no sale term.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from bot.formatters import token_card
from bot.token.record import load_record

#: The record as the website publishes it: no sale block, and only the audit
#: of the program that holds the locks.
PUBLISHED = Path(__file__).resolve().parents[1] / "app" / "content" / "rclaw.mainnet.json"

ROOT = Path(__file__).resolve().parents[1]
READMES = ("README.md", "README.zh-TW.md")
SITE = "https://www.humanoid-traders.com"
DEPLOY_HOST = "pmvc58g2.mule.page"
# A run of base58 long enough to be an account: words, slugs and URLs split
# into shorter runs at their separators.
ADDRESS = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")


def _section(name: str) -> str:
    text = (ROOT / name).read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(r"<!-- rclaw-record:start", text)]
    ends = [m.start() for m in re.finditer(r"<!-- rclaw-record:end -->", text)]
    assert len(starts) == 1 and len(ends) == 1, f"{name}: {len(starts)} starts, {len(ends)} ends"
    assert starts[0] < ends[0]
    return text[starts[0]:ends[0]]


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d[\d,.]*\d|\d", text)


@pytest.fixture(scope="module")
def record():
    return load_record(PUBLISHED)


@pytest.mark.parametrize("name", READMES)
def test_the_mint_is_the_records_and_links_its_own_explorer_page(name, record):
    section = _section(name)
    mint = record["mint"]
    assert f"**`{mint}`**" in section
    assert f"https://solscan.io/token/{mint}" in section
    assert "1,000,000,000" in section and int(record["supply_tokens"]) == 1_000_000_000


@pytest.mark.parametrize("name", READMES)
def test_every_address_in_the_section_is_one_the_record_names(name, record):
    # A mistyped or forged address is the failure that matters on this page,
    # and it would not match any field: hold the whole section to the record.
    known = ({record["mint"]} | {i["vesting_account"] for i in record["locks"]["items"]}
             | {record["multisig"]["vault"], record["multisig"]["account"]})
    found = set(ADDRESS.findall(_section(name)))
    assert found <= known, f"{name}: addresses the record does not name: {sorted(found - known)}"
    assert known <= found, f"{name}: record addresses missing: {sorted(known - found)}"


@pytest.mark.parametrize("name", READMES)
def test_the_section_names_no_sale(name, record):
    section = _section(name)
    assert "presale" not in record, "the published record carries a sale block"
    # The tooling's npm scripts are named presale:* (token/package.json); they are commands, not a sale.
    prose = re.sub(r"`npm run presale:[a-z-]+`", "", section)
    assert not re.search(r"presale|pre-sale|預售|smithii\.io/launch|per SOL|hard cap|硬頂", prose, re.I), name
    # The only Smithii links left are the locks' certificates.
    links = re.findall(r"https://[^\s*)<>\]`]+", section)
    smithii = {u for u in links if "smithii.io" in u}
    assert smithii == {i["certificate_url"] for i in record["locks"]["items"]}, (name, smithii)
    assert not re.search(r"\$\s?\d", section), "no dollar amount on a public page"


@pytest.mark.parametrize("name", READMES)
def test_every_lock_is_listed_with_its_account_certificate_amount_and_unlocks(name, record):
    section = _section(name)
    rows = [line for line in section.splitlines() if line.startswith("| ") and "solscan.io/account/" in line]
    items = record["locks"]["items"]
    assert len(rows) == len(items), (name, len(rows))
    for item, row in zip(items, rows):
        account = item["vesting_account"]
        assert f"[`{account}`](https://solscan.io/account/{account})" in row, (name, item["bucket"])
        assert token_card.is_certificate_url(item["certificate_url"])
        assert f"]({item['certificate_url']})" in row, (name, item["bucket"])
        assert f"| {int(item['tokens']):,} |" in row, (name, item["bucket"])
        unlocks = " · ".join(f"{u['at'][:10]} {u['percent']}%" for u in item["unlocks"])
        assert f"| {unlocks} |" in row, (name, item["bucket"])
        assert all(u["at"].endswith("T14:00:00Z") for u in item["unlocks"]), "the column header says 14:00 UTC"


@pytest.mark.parametrize("name", READMES)
def test_the_page_links_the_sites_own_host(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert f'<a href="{SITE}/">' in text, "the header's website link"
    assert f"{SITE}/token" in _section(name)
    assert DEPLOY_HOST not in text, "the hosting provider's hostname is not the site's address"


@pytest.mark.parametrize("name", READMES)
def test_the_treasury_multisig_is_the_records(name, record):
    ms = record["multisig"]
    vault, account = ms["vault"], ms["account"]
    lines = [line for line in _section(name).splitlines() if f"solscan.io/account/{vault}" in line]
    assert len(lines) == 1, f"{name}: {len(lines)} lines link the vault"
    line = lines[0]
    assert f"[`{vault}`](https://solscan.io/account/{vault})" in line
    assert f"[`{account}`](https://solscan.io/account/{account})" in line
    numbers = _numbers(line)
    hours, rest = divmod(ms["time_lock_seconds"], 3600)
    assert rest == 0, "the README states the time lock in hours"
    for figure in ([f"{int(ms['holds_tokens']):,}", str(ms["threshold"]), str(len(ms["members"])), str(hours)]
                   + [f"{int(b['tokens']):,}" for b in ms["buckets"]]):
        assert figure in numbers, (name, figure)
    assert ms["verified_at"] in line, (name, "the date the balance was read")
    # The members' wallets are in the record for the verifier, not on this page.
    assert not any(m in line for m in ms["members"])
    if name == "README.md":
        assert f"{ms['threshold']} of {len(ms['members'])} members and then a {hours}-hour time lock" in line
        for b in ms["buckets"]:
            assert f"{b['bucket']} {int(b['tokens']):,}" in line, b["bucket"]
        assert "not a lock" in line


@pytest.mark.parametrize("name", READMES)
def test_smithii_s_audits_are_the_records_and_say_what_they_cover(name, record):
    section = _section(name)
    for item in record["smithii_audits"]["items"]:
        assert f"]({item['url']})" in section, (name, item["url"])
    links = re.findall(r"\]\((https://[^)\s]+)\)", section)
    audit_links = {u for u in links if "halborn.com" in u or "CoinFabrik" in u}
    assert audit_links == {i["url"] for i in record["smithii_audits"]["items"]}, (name, audit_links)
    assert audit_links, f"{name}: the locks' program audit is gone too"
    if name == "README.md":
        assert "not of RUNECLAW" in section and "not shown to be the audited code" in section
        assert "the sale program" not in section
