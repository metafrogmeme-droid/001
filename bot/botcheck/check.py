"""Public signals for one bot name. Read-only.

The flag is off unless ``BOTCHECK_ENABLED`` is exactly ``1``, ``true``,
``yes``, or ``on`` (any case, surrounding space ignored). Every other value,
including unset, leaves it off.

A submission is one line in the review queue. The line has a time, the name,
and where it came from (``web`` or ``telegram``). It does not have a Telegram
id, and the catalog is never rewritten from it.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

FLAG = "BOTCHECK_ENABLED"
ON_VALUES = frozenset({"1", "true", "yes", "on"})

SIGNAL_IDS = (
    "creates_wallets",
    "asks_seed",
    "code_public",
    "signs_from",
    "public_incident",
)
YES_NO = frozenset({"yes", "no", "unknown"})
SIGNING = frozenset({"bot_controlled", "user_wallet", "unknown"})
MAINTAINED = frozenset({"yes", "no", "unknown"})
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .@_-]{0,63}$")
_DIGITS = re.compile(r"\d{5,}")
# Words the check must not say. Submissions that contain one are refused
# without being echoed back.
_BANNED = re.compile(r"\b(?:safe|scam|verified|trusted)\b", re.I)
_MONEY = re.compile(r"\$|RCLAW|%|\bstake\b", re.I)

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"

_SIGNING_TEXT = {
    "bot_controlled": "a wallet the bot can sign",
    "user_wallet": "the user's own wallet",
    "unknown": "unknown",
}


class CatalogError(Exception):
    """The catalog could not be read. The message is a fixed token, not the file."""


def enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only for 1 / true / yes / on. Unset and everything else are off."""
    src = os.environ if env is None else env
    raw = str(src.get(FLAG, "") or "").strip().lower()
    return raw in ON_VALUES


def queue_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """Where a submission is appended. Relative paths are from the repo root."""
    src = os.environ if env is None else env
    custom = str(src.get("BOTCHECK_QUEUE_PATH", "") or "").strip()
    if custom:
        path = Path(custom)
        return path if path.is_absolute() else ROOT / path
    base = str(src.get("DAILY_DUEL_STATE_DIR", "") or "").strip()
    if base:
        root = Path(base)
        if not root.is_absolute():
            root = ROOT / root
        return root / "botcheck_review.jsonl"
    return ROOT / "data" / "botcheck" / "review.jsonl"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize(query: str) -> str:
    text = re.sub(r"\s+", " ", str(query or "").strip().lower())
    if text.startswith("@"):
        text = text[1:].strip()
    return text


def answer_text(signal_id: str, answer: str) -> str:
    if signal_id == "signs_from":
        return _SIGNING_TEXT[answer]
    return answer


def _require(cond: bool) -> None:
    if not cond:
        raise CatalogError("catalog")


def load(path: Optional[Path] = None) -> dict[str, Any]:
    """The catalog, or CatalogError. A bad file is not an empty catalog."""
    src = path or CATALOG_PATH
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError("catalog") from exc
    if not isinstance(raw, dict):
        raise CatalogError("catalog")
    _validate(raw)
    return raw


def _validate(cat: Mapping[str, Any]) -> None:
    as_of = cat.get("as_of")
    if not isinstance(as_of, str) or _DATE.fullmatch(as_of) is None:
        raise CatalogError("catalog")
    _require(cat.get("ai_written_label") == "AI-written")
    notice = cat.get("ai_written_notice")
    _require(isinstance(notice, str) and notice.startswith("AI-written") and "Art. 50" in notice)
    disclaimer = cat.get("disclaimer")
    if not isinstance(disclaimer, str) or "Unknown stays unknown" not in disclaimer or as_of not in disclaimer:
        raise CatalogError("catalog")
    specs = cat.get("signals")
    if not isinstance(specs, list):
        raise CatalogError("catalog")
    spec_ids: list[Any] = []
    for spec in specs:
        if not isinstance(spec, Mapping):
            raise CatalogError("catalog")
        spec_ids.append(spec.get("id"))
        question = spec.get("question")
        _require(isinstance(question, str) and bool(question.strip()))
    _require(spec_ids == list(SIGNAL_IDS))
    bots = cat.get("bots")
    if not isinstance(bots, list) or len(bots) < 20:
        raise CatalogError("catalog")
    seen: set[str] = set()
    for bot in bots:
        _require(isinstance(bot, dict))
        bid = bot.get("id")
        _require(isinstance(bid, str) and bid not in seen and bool(re.fullmatch(r"[a-z0-9-]{2,64}", bid)))
        seen.add(bid)
        _require(isinstance(bot.get("name"), str) and bool(bot["name"].strip()))
        for key in ("aliases", "usernames"):
            vals = bot.get(key)
            _require(isinstance(vals, list) and all(isinstance(v, str) and v.strip() for v in vals))
        signals = bot.get("signals")
        _require(isinstance(signals, dict) and set(signals) == set(SIGNAL_IDS))
        for sid in SIGNAL_IDS:
            row = signals[sid]
            _require(isinstance(row, dict))
            answer = row.get("answer")
            allowed = SIGNING if sid == "signs_from" else YES_NO
            _require(answer in allowed)
            _require(isinstance(row.get("detail"), str) and bool(row["detail"].strip()))
            url = row.get("source_url")
            _require(isinstance(url, str) and url.startswith("https://") and " " not in url)
            _require(isinstance(row.get("source_label"), str) and bool(row["source_label"].strip()))
            _require(row.get("as_of") == as_of)
            if sid == "code_public":
                _require(row.get("maintained") in MAINTAINED)
                extra = row.get("maintained_source_url")
                if extra is not None:
                    _require(isinstance(extra, str) and extra.startswith("https://") and " " not in extra)
                    label = row.get("maintained_source_label")
                    _require(isinstance(label, str) and bool(label.strip()))
            else:
                _require("maintained" not in row)


def _index(cat: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for bot in cat["bots"]:
        keys = [bot["id"], bot["name"], *bot.get("aliases", []), *bot.get("usernames", [])]
        for key in keys:
            norm = normalize(key)
            if norm and norm not in out:
                out[norm] = bot
    return out


def _summary(as_of: str, signals: list[dict[str, Any]]) -> str:
    bits = [f"Public signals as of {as_of}."]
    for row in signals:
        if row["id"] == "code_public":
            bits.append(f"Code public: {row['answer_text']}. Maintained: {row['maintained']}.")
        elif row["id"] == "signs_from":
            bits.append(f"Signing: {row['answer_text']}.")
        else:
            bits.append(f"{row['question']} {row['answer_text']}.")
    return " ".join(bits)


def to_public(bot: Mapping[str, Any], cat: Mapping[str, Any]) -> dict[str, Any]:
    specs = {s["id"]: s["question"] for s in cat["signals"]}
    signals: list[dict[str, Any]] = []
    for sid in SIGNAL_IDS:
        raw = bot["signals"][sid]
        item: dict[str, Any] = {
            "id": sid,
            "question": specs[sid],
            "answer": raw["answer"],
            "answer_text": answer_text(sid, raw["answer"]),
            "detail": raw["detail"],
            "source_url": raw["source_url"],
            "source_label": raw["source_label"],
            "as_of": raw["as_of"],
        }
        if sid == "code_public":
            item["maintained"] = raw["maintained"]
            if raw.get("maintained_source_url"):
                item["maintained_source_url"] = raw["maintained_source_url"]
                item["maintained_source_label"] = raw["maintained_source_label"]
        signals.append(item)
    return {
        "id": bot["id"],
        "name": bot["name"],
        "usernames": list(bot.get("usernames") or []),
        "signals": signals,
        "summary": {
            "text": _summary(str(cat["as_of"]), signals),
            "label": cat["ai_written_label"],
            "notice": cat["ai_written_notice"],
        },
    }


def public_document(catalog: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    cat = catalog if catalog is not None else load()
    return {
        "ok": True,
        "as_of": cat["as_of"],
        "disclaimer": cat["disclaimer"],
        "ai_written_label": cat["ai_written_label"],
        "ai_written_notice": cat["ai_written_notice"],
        "bots": [to_public(bot, cat) for bot in cat["bots"]],
    }


def _refused(query: str) -> Optional[str]:
    text = str(query or "")
    if _BANNED.search(text) or _MONEY.search(text):
        return "That name cannot be checked."
    return None


def lookup(query: str, catalog: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    refused = _refused(query)
    if refused:
        return {"ok": False, "error": refused}
    try:
        cat = catalog if catalog is not None else load()
    except CatalogError:
        return {"ok": False, "error": "The check could not be read."}
    norm = normalize(query)
    if not norm:
        return {"ok": False, "error": "Send a bot name."}
    bot = _index(cat).get(norm)
    body: dict[str, Any] = {
        "ok": True,
        "as_of": cat["as_of"],
        "disclaimer": cat["disclaimer"],
        "query": norm,
        "found": bot is not None,
        "bot": to_public(bot, cat) if bot is not None else None,
    }
    return body


def render_text(result: Mapping[str, Any]) -> str:
    """The Telegram reply. One bot, or the list, or a fixed refusal."""
    if not result.get("ok"):
        return str(result.get("error") or "The check could not be read.")
    bot = result.get("bot")
    if not isinstance(bot, dict):
        return (
            f"{result.get('disclaimer')}\n\n"
            "No curated entry for that name.\n"
            "It is unknown. Nothing here was filled in.\n"
            "/botcheck submit <name> queues it for review and does not publish it."
        )
    lines = [
        str(result.get("disclaimer") or ""),
        bot["name"],
        "",
    ]
    handles = bot.get("usernames") or []
    if handles:
        lines.append(" ".join(f"@{h.lstrip('@')}" for h in handles))
        lines.append("")
    for row in bot["signals"]:
        lines.append(row["question"])
        lines.append(row["answer_text"])
        if row["id"] == "code_public":
            lines.append(f"Maintained: {row['maintained']}")
        lines.append(row["detail"])
        lines.append(f"Source: {row['source_label']}")
        lines.append(row["source_url"])
        extra = row.get("maintained_source_url")
        if extra:
            lines.append(f"Maintained source: {row.get('maintained_source_label')}")
            lines.append(extra)
        lines.append(f"as of {row['as_of']}")
        lines.append("")
    summary = bot.get("summary") or {}
    lines.append(str(summary.get("notice") or ""))
    lines.append(str(summary.get("text") or ""))
    return "\n".join(lines).rstrip() + "\n"


def list_text(catalog: Optional[Mapping[str, Any]] = None) -> str:
    try:
        cat = catalog if catalog is not None else load()
    except CatalogError:
        return "The check could not be read."
    names = "\n".join(f"- {bot['name']}" for bot in cat["bots"])
    return (
        f"{cat['disclaimer']}\n"
        "Send /botcheck and a name.\n\n"
        f"{names}\n\n"
        "A name that is not listed is unknown. "
        "/botcheck submit <name> queues it for review and does not publish it."
    )


def _submission_error(query: str, note: str) -> Optional[str]:
    if _BANNED.search(query) or _BANNED.search(note) or _MONEY.search(query) or _MONEY.search(note):
        return "That note cannot be queued."
    text = re.sub(r"\s+", " ", str(query or "").strip())
    if not text:
        return "Send a bot name."
    if _DIGITS.search(text) or _DIGITS.search(note):
        return "Send a bot name, not a numeric id. Nothing was stored."
    if not _NAME.fullmatch(text):
        return "Send a bot name."
    if len(note) > 200:
        return "Send a bot name."
    return None


def enqueue(
    query: str,
    *,
    note: str = "",
    via: str = "telegram",
    env: Optional[Mapping[str, str]] = None,
    queue: Optional[Path] = None,
    now: Optional[str] = None,
    catalog: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Append one review line, or refuse. Never publishes, never stores an id."""
    cleaned_note = str(note or "").replace("\n", " ").strip()
    problem = _submission_error(query, cleaned_note)
    if problem:
        return {"ok": False, "queued": False, "error": problem}
    try:
        cat = catalog if catalog is not None else load()
    except CatalogError:
        return {"ok": False, "queued": False, "error": "The check could not be read."}
    if _index(cat).get(normalize(query)) is not None:
        return {
            "ok": True,
            "queued": False,
            "error": "That name is already in the curated list. Nothing was added.",
        }
    shown = re.sub(r"\s+", " ", query.strip())
    record: dict[str, str] = {
        "submitted_at": now or _stamp(),
        "query": shown,
        "via": via if via in ("web", "telegram") else "web",
    }
    if cleaned_note:
        record["note"] = cleaned_note
    path = queue or queue_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return {"ok": True, "queued": True}


def reply_for(
    text: str,
    *,
    env: Optional[Mapping[str, str]] = None,
    queue: Optional[Path] = None,
    now: Optional[str] = None,
) -> str:
    """What /botcheck says. ``text`` is the command argument, not a chat id."""
    raw = str(text or "").strip()
    if raw.lower().startswith("submit"):
        rest = raw.split(None, 1)[1] if len(raw.split(None, 1)) == 2 else ""
        result = enqueue(rest, via="telegram", env=env, queue=queue, now=now)
        if result.get("queued"):
            return "Queued for review. It is not published."
        return str(result.get("error") or "Nothing was queued.")
    if not raw:
        return list_text()
    try:
        return render_text(lookup(raw))
    except CatalogError:
        return "The check could not be read."
