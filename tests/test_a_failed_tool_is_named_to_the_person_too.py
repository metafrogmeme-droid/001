"""The user is told which tool failed, not "Something went wrong".

Both surfaces already wrote the honest sentence into MEMORY —
`skill_failure_memory`: "[get_portfolio] FAILED — the tool raised an error and
returned no result. Nothing was measured." — and then told the PERSON
"Something went wrong. Try again or use a command.", which names neither what
was attempted nor that nothing was read. The model's record was more honest
than the human's screen, on the same line of the same handler.

`skill_failure_notice` is the one sentence for both surfaces, and it carries
no detail from the exception for the reason the memory version gives: a driver
message can hold a URL, a host or a config value, and this goes to a user.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace as NS

from bot.nlp.skill_memory import skill_failure_memory
from bot.skills.chat_runtime import skill_failure_notice
from tests.source_scan import code_only


class TestTheNotice:
    def test_it_names_the_tool_and_says_nothing_was_measured(self):
        out = skill_failure_notice("get_portfolio")
        assert "get_portfolio" in out
        assert "nothing was measured" in out.lower()
        assert "something went wrong" not in out.lower()

    def test_an_unnamed_tool_still_says_what_happened(self):
        for name in ("", None, "   "):
            out = skill_failure_notice(name)  # type: ignore[arg-type]
            assert "that tool" in out and "nothing was measured" in out.lower()

    def test_the_name_is_escaped_because_it_is_interpolated_into_html(self):
        out = skill_failure_notice("<img src=x onerror=1>")
        assert "<img" not in out and "&lt;img" in out

    def test_it_carries_no_driver_detail(self):
        # The same rule `skill_failure_memory` states: memory and this notice
        # both reach a reader, and an exception's text is the one place a host
        # or a config value escapes.
        src = code_only(inspect.getsource(skill_failure_notice))
        assert "exc" not in src and "exception" not in src.lower()

    def test_it_agrees_with_what_memory_records(self):
        # Two readers of the same event: the model's record and the person's
        # screen must not disagree about whether anything was measured.
        mem = skill_failure_memory("get_portfolio")
        notice = skill_failure_notice("get_portfolio")
        assert "get_portfolio" in mem and "get_portfolio" in notice
        assert "nothing was measured" in mem.lower()
        assert "nothing was measured" in notice.lower()


class TestBothSurfacesUseIt:
    def test_the_web_names_the_tool_that_raised(self, monkeypatch):
        """Driven, not grepped: plant a skill that raises and read the reply."""
        import json

        from bot.web import user_gateway as ug
        from tests.test_one_answer_shape_per_turn import _Recorder, _request, _run, _web_handler

        monkeypatch.setattr(ug, "_guard_user", lambda *a, **kw: None)
        monkeypatch.setattr(ug, "_is_admin_id", lambda h, uid: False)
        monkeypatch.setattr(ug, "build_profile_note", lambda p: "")

        class _Boom:
            name = "get_portfolio"

            async def execute(self, engine, **kw):
                raise RuntimeError("socket hang up to https://gw.example/internal")

        rec = _Recorder()
        handler = _web_handler(rec)
        handler.registry = NS(get=lambda n: _Boom() if n == "get_portfolio" else None)
        handler.users.permission_denial = lambda uid, perm: None
        engine = NS(firewall_scan=lambda *a, **kw: None, _pending_ideas={})
        resp = _run(ug._chat_turn(_request(
            handler, {"telegram_id": "4242", "text": "show my portfolio"}, engine=engine)))
        assert resp.status == 200
        reply = json.loads(resp.text)["reply_html"]
        assert "get_portfolio" in reply and "nothing was measured" in reply.lower()
        assert "Something went wrong" not in reply
        # The exception's text names a host. It must not reach the screen.
        assert "gw.example" not in reply and "socket hang up" not in reply

    def test_the_telegram_path_sends_the_same_notice(self):
        src = code_only(inspect.getsource(
            __import__("bot.skills.telegram_handler", fromlist=["x"])))
        # The web is driven above; this pins that the Telegram branch reaches
        # the same leaf rather than keeping a second sentence of its own.
        assert "skill_failure_notice(intent.skill)" in src

    def test_the_apology_is_gone_from_the_skill_failure_paths(self):
        import bot.skills.telegram_handler as th
        import bot.web.user_gateway as ug
        for mod in (th, ug):
            src = code_only(inspect.getsource(mod))
            # The one remaining use is a COMMAND loader's own message, which
            # names the command it could not load — a different event.
            bad = [ln for ln in src.splitlines()
                   if "Something went wrong. Try again or use a command." in ln]
            assert bad == [], (mod.__name__, bad)
