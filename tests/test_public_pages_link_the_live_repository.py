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

The same pages name the project's X account, and the token's metadata names
it too (``token/config/rclaw-metadata.json``, ``extensions.twitter``, the
account the Smithii sale page and the lock certificates were given). The
README and the GitBook named a personal account instead; every X profile link
on a public page is held to the metadata's now.

"Docs" linked a GitBook site (``humanoid-traders-1.gitbook.io``) that is synced
from outside this repository, so nothing here could say what it showed. On
2026-10-14 the operator asked for the docs on GitHub instead: every docs link
on a public surface names ``docs/gitbook/`` in this repository, the files the
tests hold, and none names a GitBook site. The React site's committed build
(``website/``) is rebuilt with it; the archived pages keep what they said.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIVE = "github.com/metafrogmeme-droid/001"
DEAD = "github.com/Humanoid-Traders"

PUBLIC = sorted(
    {ROOT / "README.md", ROOT / "README.zh-TW.md", ROOT / "SECURITY.md", ROOT / "agent_card.json",
     ROOT / "LICENSE", ROOT / "NOTICE",
     ROOT / "app" / "public" / "js" / "i18n.js", ROOT / "app" / "lib" / "tool8257.js",
     ROOT / "app" / "lib" / "docs_pages.js", ROOT / "app" / "public" / "js" / "docs-page.js",
     ROOT / "site" / "src" / "components" / "PlatformLink.tsx",
     # website/ is site/'s committed build (CI fails when it differs); its archive/ keeps old pages as written.
     ROOT / "website" / "index.html", ROOT / "website" / "404.html"}
    | set((ROOT / "website").glob("*/index.html"))
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
    for must in ("README.md", "README.zh-TW.md", "SECURITY.md", "agent_card.json", "LICENSE", "NOTICE",
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


# A profile link, not a share or sign-in intent: x.com/<handle> or twitter.com/<handle>.
X_PROFILE = re.compile(r"https://(?:x|twitter)\.com/(?!intent/|i/|share)([A-Za-z0-9_]{1,15})\b")


def test_every_x_profile_link_is_the_tokens_own_account():
    meta = json.loads((ROOT / "token" / "config" / "rclaw-metadata.json").read_text(encoding="utf-8"))
    want = meta["extensions"]["twitter"]
    handle = X_PROFILE.fullmatch(want)
    assert handle, want
    seen = {}
    for path in PUBLIC:
        for m in X_PROFILE.finditer(path.read_text(encoding="utf-8")):
            seen.setdefault(m.group(1), set()).add(_rel(path))
    assert set(seen) <= {handle.group(1)}, f"X accounts other than {want}: {seen}"
    for name in ("README.md", "README.zh-TW.md", "docs/gitbook/README.md"):
        assert name in seen.get(handle.group(1), set()), f"{name} does not link {want}"



GITBOOK_SITE = re.compile(r"https?://[\w.-]*gitbook\.(?:io|com)\b")
DOCS = f"https://{LIVE}/tree/main/docs/gitbook"


@pytest.mark.parametrize("path", PUBLIC, ids=_rel)
def test_no_public_page_links_a_gitbook_site(path):
    found = sorted(set(GITBOOK_SITE.findall(path.read_text(encoding="utf-8"))))
    assert not found, f"{_rel(path)} links {found}; the docs are {DOCS}"


def test_every_docs_link_is_the_docs_folder_on_github():
    """Off the website, "Docs" is the folder on GitHub. On it, "Docs" is /docs, which serves the
    same folder (app/lib/docs_pages.js) and links each page's source in it."""
    assert (ROOT / "docs" / "gitbook" / "README.md").exists(), "GitHub renders the folder's README as its front page"
    for name in ("README.md", "README.zh-TW.md", "site/src/components/PlatformLink.tsx", "website/index.html"):
        assert DOCS in (ROOT / name).read_text(encoding="utf-8"), f"{name} does not link {DOCS}"
    card = json.loads((ROOT / "agent_card.json").read_text(encoding="utf-8"))
    assert card["documentation"] == DOCS
    for name in ("app/public/index.html", "app/public/explore.html"):
        html = (ROOT / name).read_text(encoding="utf-8")
        links = re.findall(r'<a [^>]*data-i18n="nav\.docs"[^>]*>', html)
        assert links and all(a.startswith('<a href="/docs" ') for a in links), f"{name}: {links}"
    for name in ("app/lib/docs_pages.js", "app/public/js/docs-page.js"):
        assert DOCS in (ROOT / name).read_text(encoding="utf-8"), f"{name} does not link {DOCS}"


DEPLOY_HOST = "pmvc58g2.mule.page"


@pytest.mark.parametrize("path", PUBLIC, ids=_rel)
def test_no_public_page_names_the_deployment_host(path):
    """The site's address is www.humanoid-traders.com. The hosting provider's hostname still answers (it
    is the proxy target), but a page that links it sends readers to an address that is not the brand,
    and the docs' Quick Links did exactly that until 2026-10-15."""
    assert DEPLOY_HOST not in path.read_text(encoding="utf-8"), f"{_rel(path)} names {DEPLOY_HOST}"


def _docs_entries(text: str) -> list[tuple[str, str]]:
    return re.findall(r"^\s*[*-]\s+\[([^\]]+)\]\(([^)\s]+\.md)\)", text, re.M)


def test_the_docs_front_page_lists_every_page_in_the_contents_order():
    """The front page is what GitHub shows for the folder; its list named 7 of the 21 pages."""
    folder = ROOT / "docs" / "gitbook"
    contents = [e for e in _docs_entries((folder / "SUMMARY.md").read_text(encoding="utf-8")) if e[1] != "README.md"]
    front = (folder / "README.md").read_text(encoding="utf-8")
    section = front.split("## Documentation Sections", 1)[1].split("\n## ", 1)[0]
    assert _docs_entries(section) == contents


def test_the_docs_front_page_carries_the_readmes_disclaimer():
    """One disclaimer, not two that drift: the docs' said "educational hackathon prototype" while the
    README said the software trades live with small positions."""
    def disclaimer(text: str) -> str:
        m = re.search(r"^> \*\*DISCLAIMER:\*\*.*?(?=\n\n|\Z)", text, re.S | re.M)
        assert m, "no disclaimer"
        return " ".join(m.group(0).replace("> ", " ").split())
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    front = (ROOT / "docs" / "gitbook" / "README.md").read_text(encoding="utf-8")
    assert disclaimer(front) == disclaimer(readme)

