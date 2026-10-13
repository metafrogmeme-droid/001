"""Every public page links the repository that exists, and files that exist in it.

The ``Humanoid-Traders`` GitHub organisation was suspended on 2026-08-02
(docs/AUDIT_2026-08-12.md, M27) and the code has lived at
``github.com/metafrogmeme-droid/001`` since. Two months later the README's
header, CI badge and clone command, SECURITY.md, the GitBook pages, the agent
card, the tool manifest, and the website's footer and developer page still
linked the suspended organisation: "step 1 fails for every reader", and every
"GitHub" link on the site answered 404.

A link is a claim that something is there. This file holds the public
surfaces to it: none links the suspended organisation, and every link into
this repository (``/blob/main/<path>`` or ``/tree/main/<path>``) and every
relative link in a README names a path that exists. Historical records (the
audits, the hackathon submission, archived pages) keep the URLs they were
written with and are not scanned.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIVE = "github.com/metafrogmeme-droid/001"
DEAD = "github.com/Humanoid-Traders"

PUBLIC = sorted(
    {ROOT / "README.md", ROOT / "README.zh-TW.md", ROOT / "SECURITY.md", ROOT / "agent_card.json",
     ROOT / "app" / "public" / "js" / "i18n.js", ROOT / "app" / "lib" / "tool8257.js"}
    | set((ROOT / "docs" / "gitbook").glob("*.md"))
    | set((ROOT / "app" / "public").glob("*.html"))
)
REPO_LINK = re.compile(re.escape(LIVE) + r"/(?:blob|tree)/main/([^\s\"'<>)#?\\]+)")
MD_RELATIVE = re.compile(r"\]\((?!https?:|#|mailto:)([^)\s#]+)")
HTML_RELATIVE = re.compile(r'(?:href|src)="(?!https?:|#|mailto:|/)([^"#?]+)"')


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def test_the_scan_reaches_the_surfaces_it_names():
    names = {_rel(p) for p in PUBLIC}
    for must in ("README.md", "README.zh-TW.md", "SECURITY.md", "agent_card.json",
                 "app/public/index.html", "app/public/developers.html", "docs/gitbook/getting-started.md"):
        assert must in names, must
    assert all(p.exists() for p in PUBLIC)


@pytest.mark.parametrize("path", PUBLIC, ids=_rel)
def test_no_public_page_links_the_suspended_organisation(path):
    text = path.read_text(encoding="utf-8")
    assert DEAD not in text, f"{_rel(path)} links {DEAD}, suspended since 2026-08-02; the repository is {LIVE}"


@pytest.mark.parametrize("path", PUBLIC, ids=_rel)
def test_every_link_into_the_repository_names_a_file_that_exists(path):
    text = path.read_text(encoding="utf-8")
    missing = sorted({m.group(1) for m in REPO_LINK.finditer(text) if not (ROOT / m.group(1)).exists()})
    assert not missing, f"{_rel(path)} links files the repository does not have: {missing}"


@pytest.mark.parametrize("name", ["README.md", "README.zh-TW.md"])
def test_every_relative_link_in_a_readme_resolves(name):
    path = ROOT / name
    text = path.read_text(encoding="utf-8")
    targets = {m.group(1) for m in MD_RELATIVE.finditer(text)} | {m.group(1) for m in HTML_RELATIVE.finditer(text)}
    missing = sorted(t for t in targets if not (path.parent / t).resolve().exists())
    assert not missing, f"{name}: {missing}"


@pytest.mark.parametrize("name", ["README.md", "README.zh-TW.md", "docs/gitbook/getting-started.md"])
def test_the_clone_command_clones_the_live_repository_into_the_folder_the_next_step_enters(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    clones = re.findall(r"git clone (\S+)(?: (\S+))?\n(?:cd (\S+))?", text)
    assert clones, name
    for url, folder, cd in clones:
        assert url == f"https://{LIVE}.git", (name, url)
        if cd:
            assert folder == cd, (name, folder, cd)
