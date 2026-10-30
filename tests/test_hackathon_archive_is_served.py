"""The marketing footer's Hackathon archive link has a page behind it.

website/archive/hackathon/index.html is the frozen submission. A request for
/archive/hackathon that falls through to the marketing app shell is painted
as that app's 404, because the shell has no such route. The bridge names the
file, and nginx does too.
"""
from __future__ import annotations

import os
import secrets

import pytest


@pytest.fixture(scope="module")
def bridge_app():
    os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))
    import api_bridge
    return api_bridge.app


def test_the_archive_route_serves_the_frozen_page(bridge_app):
    from fastapi.testclient import TestClient

    client = TestClient(bridge_app)
    for path in ("/archive/hackathon", "/archive/hackathon/"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert "not current" in res.text.lower(), path
        assert "Bitget" in res.text or "Hackathon" in res.text


def test_nginx_does_not_hand_the_archive_to_the_app_shell():
    from pathlib import Path
    conf = Path(__file__).resolve().parents[1].joinpath("nginx.conf").read_text(encoding="utf-8")
    assert "location = /archive/hackathon" in conf
    assert "/archive/hackathon/index.html" in conf
