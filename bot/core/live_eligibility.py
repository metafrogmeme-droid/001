"""Whether the running strategy may place live orders with no human confirm.

Autonomous live orders (the tick's auto-confirm and the ``/forcescan`` loop,
both of which confirm under ``user_id="auto"``) used to need one thing: the
``AUTO_CONFIRM_LIVE_ENABLED`` flag, which ships ON in the code. A flag can be
flipped back by mistake, and the ``/forcescan`` loop never read the tick's
suppression at all; it was stopped only at the compliance Lock-5 mint, by the
same flag. So nothing in code required evidence that the strategy works before
it traded real money on its own.

It does now. A non-human live confirm needs BOTH the flag and an ELIGIBILITY
RECORD: a committed file, ``benchmark/eligibility/<strategy_hash>.json``,
naming the strategy that is running and a verdict that it survives. Every
other state denies, and each has its own sentence:

- ``missing``: no record for this strategy (every deployment today);
- ``unreadable``: a file that is there and will not read;
- ``malformed``: a file that reads and does not have this schema;
- ``mismatch``: a record naming another strategy;
- ``not_survives``: the record's verdict is not "survives";
- ``no_stage``: it survives and grants no capital stage.

A human confirm is not asked: a person who taps Confirm is the decision this
record stands in for.

WHAT THE STRATEGY HASH IS, v1, AND WHAT IT CANNOT SEE. It is a sha256 over the
path and bytes of every ``.py`` file under ``bot/``. That is deliberately too
strict: any code change makes every record stale, including a change to a card
that trades nothing. Too strict fails closed, and narrowing the hash to the
strategy's own code and configuration is its own piece of work. It does NOT
cover configuration: a record written under one ``.env`` is read under another.
That is stated rather than hidden, and it is the reason a record is a committed
file a reviewer reads, not something the bot writes for itself.

WHAT A RECORD ASSERTS IS NOT CHECKED HERE. The evidence fields (the
registration commit, the prospective window, the sample, the interval, the
cost model) are recorded for a reviewer; this gate reads four fields and
trusts the review. A record is added by a commit, so it is reviewed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NamedTuple, Optional

from bot.utils.json_store import FRESH, READ, read_json_store
from bot.utils.paths import REPO_ROOT, state_path

#: Where records live, relative to the repo root.
RECORD_DIR = "benchmark/eligibility"

SCHEMA = 1

#: The capital stages a record can grant. "none" is a record that grants
#: nothing, and denies.
STAGES = ("none", "minimum", "25%", "100%")

#: The verdict words a record can carry. Only "survives" is eligible.
VERDICTS = ("survives", "does_not", "inconclusive")

ELIGIBLE = "eligible"
MISSING = "missing"
UNREADABLE = "unreadable"
MALFORMED = "malformed"
MISMATCH = "mismatch"
NOT_SURVIVES = "not_survives"
NO_STAGE = "no_stage"


class Eligibility(NamedTuple):
    state: str
    #: One sentence, no path, no dollar figure: it reaches the trade log and
    #: the refusal a confirm answers with.
    reason: str
    strategy_hash: str
    stage: Optional[str] = None

    @property
    def eligible(self) -> bool:
        return self.state == ELIGIBLE


_HASH_CACHE: Optional[str] = None


def strategy_hash(root: Optional[Path] = None) -> str:
    """v1: sha256 over every ``.py`` under ``bot/``, path and bytes, sorted.

    Computed once per process for the repo root, because the code a process
    runs does not change under it. A different ``root`` is never cached.
    """
    global _HASH_CACHE
    if root is None and _HASH_CACHE is not None:
        return _HASH_CACHE
    base = Path(root) if root is not None else REPO_ROOT
    h = hashlib.sha256()
    for path in sorted((base / "bot").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(base).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    digest = h.hexdigest()
    if root is None:
        _HASH_CACHE = digest
    return digest


def record_path(strategy: str, root: Optional[Path] = None) -> Path:
    rel = f"{RECORD_DIR}/{strategy}.json"
    if root is not None:
        return Path(root) / rel
    return state_path(rel)


def read_eligibility(strategy: Optional[str] = None,
                     root: Optional[Path] = None) -> Eligibility:
    """The running strategy's eligibility for autonomous live orders.

    Never raises: a fault anywhere is a state that denies.
    """
    try:
        running = strategy if strategy is not None else strategy_hash(root)
    except Exception as exc:  # noqa: BLE001 -- a hash nobody could take denies
        return Eligibility(UNREADABLE,
                           f"the running strategy could not be identified ({type(exc).__name__})",
                           "")
    short = running[:12]
    got = read_json_store(record_path(running, root))
    if got.state == FRESH:
        return Eligibility(MISSING,
                           f"no eligibility record exists for the running strategy ({short})",
                           running)
    if got.state != READ:
        return Eligibility(UNREADABLE,
                           f"the eligibility record for the running strategy ({short}) "
                           f"could not be read ({got.detail})",
                           running)
    data = got.data
    if data.get("schema") != SCHEMA or isinstance(data.get("schema"), bool):
        return Eligibility(MALFORMED,
                           f"the eligibility record for {short} is not schema {SCHEMA}",
                           running)
    named = data.get("strategy_hash")
    if not isinstance(named, str) or not named:
        return Eligibility(MALFORMED,
                           f"the eligibility record for {short} names no strategy",
                           running)
    if named != running:
        return Eligibility(MISMATCH,
                           f"the eligibility record filed for {short} names another "
                           f"strategy ({named[:12]})",
                           running)
    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        return Eligibility(MALFORMED,
                           f"the eligibility record for {short} carries no verdict this "
                           f"build can read",
                           running)
    if verdict != "survives":
        return Eligibility(NOT_SURVIVES,
                           f"the eligibility record for {short} says the strategy "
                           f"{'does not survive' if verdict == 'does_not' else 'is inconclusive'}",
                           running)
    stage = data.get("stage")
    if stage not in STAGES:
        return Eligibility(MALFORMED,
                           f"the eligibility record for {short} grants a stage this build "
                           f"cannot read",
                           running)
    if stage == "none":
        return Eligibility(NO_STAGE,
                           f"the eligibility record for {short} grants no capital stage",
                           running, stage)
    return Eligibility(ELIGIBLE,
                       f"the running strategy ({short}) holds an eligibility record "
                       f"granting the {stage} stage",
                       running, stage)
