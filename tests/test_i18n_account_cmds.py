"""
i18n routing for the account commands (/link, /unlink, /me, /sync) in
user_middleware.py.

These live in the SQLite-backed middleware, which has no UserContext access to
the JSON UserStore where the language pref lives — so _user_lang() reads it
directly (read-only, fail-safe to English). The strings are routed through t()
with English byte-identity; zh users get translations.
"""

from bot.skills.user_middleware import _user_lang
from bot.utils.i18n import _STRINGS, t

URL = "https://x.page"
L = "en"


def _eq(got, exp):
    assert got == exp, f"\n got={got!r}\n exp={exp!r}"


def test_user_lang_fails_safe():
    # Unknown / unresolved chat id must default to English, never raise.
    assert _user_lang("000000") == "en"
    assert _user_lang(None) == "en"


def test_link_messages():
    _eq(t("link_already_linked", L),
        "This Telegram is already linked to a RUNECLAW account.\n"
        "Use /unlink to disconnect first.")
    _eq(t("link_prompt", L, url=URL),
        f"Link your RUNECLAW account\n\n1. Register / log in at {URL}\n"
        "2. Go to Dashboard and copy your link token\n3. Send: /link <token>")
    _eq(t("link_token_invalid", L, url=URL),
        f"Token invalid or expired (tokens last 10 minutes).\nGenerate a new one at {URL}")
    _eq(t("link_validate_failed", L), "Could not validate token. Please try again in a moment.")
    _eq(t("link_unreachable", L),
        "Could not reach the website to validate your token.\nPlease try again in a moment.")
    _eq(t("link_other_account", L),
        "This Telegram account is already linked to another RUNECLAW account.\n"
        "Use /unlink first, then link the correct account.")
    # The old copy ended "You now have full access to RUNECLAW. Try: /scan
    # /portfolio /fullscan" — false on every deployment with an allowlist
    # (i.e. every live one). Linking joins a WEBSITE account to this chat and
    # grants no bot command; all three were refused on the next tap. What it
    # must now do is name what linking IS for and keep bot access separate.
    # tests/test_account_linking.py holds the properties; this pins the bytes.
    _eq(t("link_success", L, email="a@b.c", plan="pro", url=URL),
        "Linked.\n\nAccount: a@b.c\nPlan: pro\n\n"
        f"Your dashboard at {URL} now mirrors this chat. /me shows the account.\n\n"
        "Bot commands are separate — if /scan says you're not approved,"
        " that's the operator's allowlist, not this link.")


def test_unlink_messages():
    _eq(t("unlink_not_linked", L), "This Telegram is not linked to any account.")
    _eq(t("unlink_success", L, email="a@b.c"),
        "Unlinked from a@b.c.\nYour data is preserved. Use /link to reconnect.")


def test_me_and_sync():
    # /me carries no balance, P&L or trade count: those came from a table
    # nothing writes (tests/test_me_shows_no_figure_nobody_recorded.py).
    _eq(t("me_account", L, email="a@b.c", plan="pro", llm="openai", notif="on",
          url=URL),
        "<b>Your RUNECLAW Account</b>\n\nEmail:    <code>a@b.c</code>\n"
        "Plan:     <code>pro</code>\n\n"
        "LLM: <code>openai</code> | Notifications: <code>on</code>\n\n"
        "Your balance and trades are not stored with this account. Your"
        f" dashboard reads them from the bot each time it loads: {URL}/dashboard")
    # /sync pushes nothing now: it used to answer "Dashboard synced. Equity:
    # $10000.00" over a push of an unwritten table's defaults onto the AGENT's
    # record (tests/test_a_linked_users_sync_does_not_touch_the_agents_record.py).
    _eq(t("sync_nothing_to_push", L, url=URL),
        "Nothing to push. Your dashboard asks the bot for your account each"
        " time it loads, so there is no separate copy to update \u2014 nothing"
        f" was sent.\n\nView it at: {URL}/dashboard")
    assert "sync_success" not in _STRINGS and "sync_failed" not in _STRINGS


def test_keys_have_chinese():
    for k in ("link_already_linked", "link_prompt", "link_token_invalid",
              "link_validate_failed", "link_unreachable", "link_other_account",
              "link_success", "unlink_not_linked", "unlink_success", "me_account",
              "sync_nothing_to_push"):
        assert k in _STRINGS and _STRINGS[k]["zh"].strip(), k
        assert t(k, "zh") != t(k, "en") or "{" in _STRINGS[k]["en"], k
