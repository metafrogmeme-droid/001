"""Eight modules each knew the website's address, and each one separately.

    os.getenv("WEBSITE_URL", "https://<deployment-host>")

…copied into scan_skill (x2), telegram_handler (x3), user_middleware,
website_sync and credential_pull. `app/lib/public_origin.js` exists precisely
because this went wrong once on the web side: its header records a phone-link
QR that shipped pointing at an internal VPC hostname while every server-side
test passed. The bot had the same problem, one process over, eight times.

It matters because these addresses are consumed OUTSIDE this process — links
a user taps in Telegram, and the sync POSTs that carry the bot's data to the
site — so a stale copy fails somewhere nobody is looking.

RUNECLAW now has its own domain (www.humanoid-traders.com, verified live
2026-07-30) alongside the hosting URL it was born on. The point of the helper
is that moving between them is ONE edit, and that a test can prove no copy
survived.

The default deliberately does NOT change here: WEBSITE_URL also routes the
bot's sync POSTs, and flipping a default would move that traffic to a
different edge without anyone choosing to. Behaviour is byte-identical until
the operator sets WEBSITE_URL.
"""
from __future__ import annotations

import pathlib
import re

from bot.utils.site_url import DEFAULT_SITE_URL, PRODUCT_SITE_URL, site_url

BOT = pathlib.Path("bot")


class TestResolution:
    def test_website_url_wins(self, monkeypatch):
        monkeypatch.setenv("WEBSITE_URL", "https://a.example")
        monkeypatch.setenv("PUBLIC_ORIGIN", "https://b.example")
        assert site_url() == "https://a.example"

    def test_falls_through_in_the_documented_order(self, monkeypatch):
        monkeypatch.delenv("WEBSITE_URL", raising=False)
        monkeypatch.setenv("PUBLIC_ORIGIN", "https://b.example")
        monkeypatch.setenv("APP_BASE_URL", "https://c.example")
        assert site_url() == "https://b.example"
        monkeypatch.delenv("PUBLIC_ORIGIN")
        assert site_url() == "https://c.example"

    def test_the_order_matches_the_web_side(self):
        # A bot and a website that disagree about their own address is the
        # failure this shares one order to prevent.
        js = pathlib.Path("app/lib/public_origin.js").read_text(encoding="utf-8")
        assert "PUBLIC_ORIGIN || e.APP_BASE_URL || e.WEBSITE_URL" in js
        for key in ("WEBSITE_URL", "PUBLIC_ORIGIN", "APP_BASE_URL"):
            assert key in pathlib.Path("bot/utils/site_url.py").read_text(
                encoding="utf-8")

    def test_trailing_slashes_are_stripped(self, monkeypatch):
        # Callers append "/dashboard"; a doubled slash is an ugly 404.
        monkeypatch.setenv("WEBSITE_URL", "https://a.example///")
        assert site_url() == "https://a.example"

    def test_blank_and_whitespace_fall_through(self, monkeypatch):
        monkeypatch.setenv("WEBSITE_URL", "   ")
        monkeypatch.delenv("PUBLIC_ORIGIN", raising=False)
        monkeypatch.delenv("APP_BASE_URL", raising=False)
        assert site_url() == DEFAULT_SITE_URL

    def test_never_returns_empty(self, monkeypatch):
        # "" produces a relative-looking URL that fails silently in Telegram.
        for k in ("WEBSITE_URL", "PUBLIC_ORIGIN", "APP_BASE_URL"):
            monkeypatch.delenv(k, raising=False)
        assert site_url().startswith("https://")


class TestNoCopiesSurvive:
    def test_no_module_hardcodes_the_deployment_host(self):
        offenders = []
        for p in BOT.rglob("*.py"):
            if p.name == "site_url.py":
                continue
            if "pmvc58g2" in p.read_text(encoding="utf-8"):
                offenders.append(str(p))
        assert offenders == [], (
            f"the deployment host is hardcoded outside the helper: {offenders} — "
            "eight copies is how one of them goes stale unnoticed"
        )

    def test_no_module_reads_website_url_directly(self):
        pat = re.compile(r'getenv\(\s*["\']WEBSITE_URL')
        offenders = [str(p) for p in BOT.rglob("*.py")
                     if p.name != "site_url.py" and pat.search(
                         p.read_text(encoding="utf-8"))]
        assert offenders == [], (
            f"these bypass the resolver and miss PUBLIC_ORIGIN/APP_BASE_URL: "
            f"{offenders}"
        )


class TestTheDefaultIsUnchanged:
    def test_default_is_still_the_deployment_host(self):
        # Byte-identical to the eight copies this replaces. Flipping it would
        # move the bot's SYNC traffic to a different edge silently.
        assert DEFAULT_SITE_URL == "https://pmvc58g2.mule.page"

    def test_the_product_domain_is_recorded_for_the_switch(self):
        assert PRODUCT_SITE_URL == "https://www.humanoid-traders.com"
