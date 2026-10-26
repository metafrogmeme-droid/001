"""One shape for a public signal. A signal is a fact, not a verdict."""
from __future__ import annotations


def sig(code: str, level: str, text: str) -> dict:
    return {"code": code, "level": level, "text": text}
