#!/usr/bin/env python3
"""Render the Telegram command reference in README.md and README.zh-TW.md
from bot/skills/command_catalog.py.

The catalogue is what /help is built from, and tests/test_command_catalog.py
pins it to the handler's registration list, so it cannot name a command that
does not exist or miss one that does. README carried three hand-typed tables
beside it — seventy-odd rows for a hundred-plus commands, several describing
behaviour the code no longer had, and a "Who" column that disagreed with the
catalogue's audiences. A second copy of an enumerable list drifts in one
direction, and this one had.

The section between the markers is REPLACED by what this prints;
tests/test_readme_commands_match_catalog.py fails on any difference — the
`known_failures.txt` rule: regenerate in the same commit.

    python3 scripts/render_readme_commands.py          # rewrite both READMEs
    python3 scripts/render_readme_commands.py --check  # exit 1 on drift
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.skills.command_catalog import GROUPS, _localize  # noqa: E402

BEGIN = "<!-- BEGIN generated: telegram commands (scripts/render_readme_commands.py) -->"
END = "<!-- END generated: telegram commands -->"

TARGETS = {
    "README.md": "en",
    "README.zh-TW.md": "zh",
}

INTRO = {
    "en": ("Every registered command, grouped the way `/help` groups them. Generated "
           "from `bot/skills/command_catalog.py`, which a test pins to the handler's "
           "registration list — edit the catalogue, not this section. Groups marked "
           "*operator only* are hidden from `/help` for everyone else, who could not "
           "run them anyway."),
    "zh": ("所有已註冊的指令，依 `/help` 的分組排列。由 `bot/skills/command_catalog.py` "
           "產生（測試會將其與處理器的註冊清單比對）——請修改目錄，而非此段落。"
           "標示*僅限操作員*的群組不會對其他人顯示於 `/help`，其他人本來也無法執行。"),
}
HEAD = {"en": ("Command", "Description"), "zh": ("指令", "說明")}
ADMIN = {"en": "operator only", "zh": "僅限操作員"}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(lang: str = "en") -> str:
    """The generated section, markers included."""
    out = [BEGIN, "", INTRO[lang], ""]
    cmd_h, desc_h = HEAD[lang]
    for title, audience, entries in GROUPS:
        heading, _ = _localize(title, "", "", lang)
        suffix = f" — *{ADMIN[lang]}*" if audience == "admin" else ""
        out += [f"### {heading}{suffix}", "", f"| {cmd_h} | {desc_h} |", "|---|---|"]
        for name, desc in entries:
            _, d = _localize(title, name, desc, lang)
            out.append(f"| `/{name}` | {_cell(d)} |")
        out.append("")
    out.append(END)
    return "\n".join(out)


def splice(text: str, lang: str) -> str:
    """`text` with the section between the markers replaced by `render(lang)`.
    ValueError when the markers are missing or out of order — a README that
    lost them must not be silently left alone."""
    a = text.find(BEGIN)
    b = text.find(END)
    if a < 0 or b < 0 or b < a:
        raise ValueError("generated-section markers missing or out of order")
    return text[:a] + render(lang) + text[b + len(END):]


def main(argv: list[str]) -> int:
    check = "--check" in argv
    drift = 0
    for rel, lang in TARGETS.items():
        path = ROOT / rel
        cur = path.read_text(encoding="utf-8")
        new = splice(cur, lang)
        if new == cur:
            continue
        drift += 1
        if check:
            print(f"out of date: {rel}")
        else:
            path.write_text(new, encoding="utf-8")
            print(f"rewrote {rel}")
    if check and drift:
        print("run: python3 scripts/render_readme_commands.py")
        return 1
    print("README command tables are up to date" if not drift else f"{drift} file(s) rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
