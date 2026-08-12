"""Writing one audit entry cost as much as everything written before it.

`AuditChain.append()` needs the previous hash and sequence number, and it got
them by iterating the whole JSONL forward — under a comment that said "read
last non-empty line efficiently". `get_entries(limit)` did worse: it parsed
EVERY line as JSON and threw all but the last `limit` away, and `append()`
reaches it on every 50th entry through `sign_latest_batch`.

Nothing rotates this file. `backup.py` only copies it.

Measured on the real class, before:

    chain length      per append
             100         0.11 ms
             500         0.21 ms
           2 000         0.66 ms
           6 000         1.86 ms

Linear, and a live close fires several appends — 12 ms at 6k entries, ten
times that at 60k, forever, on the hottest and most safety-relevant path there
is. It also made building a chain quadratic: seeding 20 000 entries did not
finish in two minutes, which is why nobody had ever measured a long one.

After, reading the tail by seeking from the end:

           1 000         0.18 ms
          10 000         0.20 ms
          50 000         0.18 ms

WHY NOT A CACHE. Holding the tail in memory is faster still and is WRONG here.
`docker-compose.yml` runs the bot alongside two api_bridge workers over one
shared `data/`, so another process can append between our calls; a stale
`prev_hash` would fork the chain and manufacture exactly the discontinuity the
chain exists to detect. Slow is recoverable, a forked chain is not. The
concurrent-writer test below is the one that would fail on a cached
implementation, and it is the reason this file leads with correctness rather
than with the timings.
"""
from __future__ import annotations

import json
import time

import pytest

from bot.utils.audit_chain import AuditChain, GENESIS_HASH, _tail_lines


def _chain(tmp_path, n=0, pad=200):
    ch = AuditChain(str(tmp_path / "chain.jsonl"))
    for i in range(n):
        ch.append(event_type="SEED", payload={"i": i, "pad": "x" * pad})
    return ch


# ── correctness comes first ──────────────────────────────────────────

def test_the_chain_still_verifies(tmp_path):
    """The whole point of the file. A faster tail read that broke the hash
    chain would be a catastrophic trade."""
    ch = _chain(tmp_path, 200)
    ok, problems = AuditChain.verify(str(tmp_path / "chain.jsonl"))
    assert ok, f"the chain no longer verifies: {problems[:3]}"


def test_the_tail_matches_a_full_forward_scan(tmp_path):
    """Read from the end must agree with read from the start, which is what
    the old implementation did."""
    ch = _chain(tmp_path, 137)
    path = tmp_path / "chain.jsonl"
    forward = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for n in (1, 2, 50, 136, 137, 500):
        assert _tail_lines(path, n) == forward[-n:], f"disagreement at n={n}"


def test_sequence_numbers_are_contiguous_across_a_chunk_boundary(tmp_path):
    """The tail read walks backwards in 64KB steps. An entry that straddles a
    step boundary must not be lost or duplicated."""
    ch = _chain(tmp_path, 400, pad=500)          # ~200KB: several chunks
    entries = ch.get_entries(limit=400)
    seqs = [e.sequence for e in entries]
    assert seqs == list(range(400)), "entries lost or reordered at a boundary"


def test_multibyte_payloads_survive_the_backwards_read(tmp_path):
    """A chunk boundary can land inside a multi-byte UTF-8 character. The cut
    is made at a newline — one ASCII byte — so the slice always begins on a
    character boundary; this drives it rather than trusting it."""
    ch = AuditChain(str(tmp_path / "chain.jsonl"))
    for i in range(300):
        ch.append(event_type="SEED",
                  payload={"i": i, "note": "利益 · Gewinn · прибыль " + "é" * 300})
    entries = ch.get_entries(limit=300)
    assert len(entries) == 300
    assert "прибыль" in entries[-1].payload["note"]
    ok, problems = AuditChain.verify(str(tmp_path / "chain.jsonl"))
    assert ok, problems[:3]


def test_a_second_writer_is_seen_immediately(tmp_path):
    """THE test that a cached tail would fail.

    Another process (an api_bridge worker) appends to the same file. Our next
    append must chain onto ITS entry, not onto the last one we happened to
    write — otherwise two entries share a prev_hash and the chain forks.
    """
    path = tmp_path / "chain.jsonl"
    ours = _chain(tmp_path, 5)
    theirs = AuditChain(str(path))               # a separate instance
    outside = theirs.append(event_type="OTHER_PROCESS", payload={})

    nxt = ours.append(event_type="MINE", payload={})
    assert nxt.prev_hash == outside.entry_hash, (
        "our append ignored another writer's entry — the chain is forked")
    assert nxt.sequence == outside.sequence + 1
    ok, problems = AuditChain.verify(str(path))
    assert ok, problems[:3]


def test_an_empty_or_missing_log_starts_at_genesis(tmp_path):
    ch = AuditChain(str(tmp_path / "nothing.jsonl"))
    first = ch.append(event_type="FIRST", payload={})
    assert first.prev_hash == GENESIS_HASH
    assert first.sequence == 0


def test_blank_lines_are_not_entries(tmp_path):
    """A stray newline in the log must not read back as an entry, and must not
    become the tail."""
    ch = _chain(tmp_path, 3)
    path = tmp_path / "chain.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n\n")
    nxt = ch.append(event_type="AFTER_BLANKS", payload={})
    assert nxt.sequence == 3
    assert len(ch.get_entries(limit=100)) == 4


def test_a_log_with_no_trailing_newline_still_reads(tmp_path):
    path = tmp_path / "chain.jsonl"
    ch = _chain(tmp_path, 2)
    text = path.read_text(encoding="utf-8").rstrip("\n")
    path.write_text(text, encoding="utf-8")      # truncate the final newline
    assert len(_tail_lines(path, 5)) == 2
    assert ch.append(event_type="X", payload={}).sequence == 2


@pytest.mark.parametrize("n", [0, -1])
def test_asking_for_no_lines_returns_none(tmp_path, n):
    ch = _chain(tmp_path, 3)
    assert _tail_lines(tmp_path / "chain.jsonl", n) == []


def test_get_entries_returns_the_last_n_in_order(tmp_path):
    ch = _chain(tmp_path, 60)
    got = ch.get_entries(limit=10)
    assert [e.sequence for e in got] == list(range(50, 60))


# ── a read that STOPS before the start of the file ───────────────────
#
# Everything above this line used logs smaller than one 64KB chunk, or asked
# for every line — so the backwards walk always ran to position 0 and the
# partial-first-line cut was never reached. A mutation pass proved the gap:
# deleting that cut, and an off-by-one in the loop bound, both survived every
# test in this file. These force the read to stop mid-file, which is the only
# place either one can show.

def test_a_small_tail_of_a_large_log_matches_a_forward_scan(tmp_path):
    ch = _chain(tmp_path, 2000, pad=500)         # ~1.2MB: many chunks
    path = tmp_path / "chain.jsonl"
    forward = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert path.stat().st_size > 5 * 64 * 1024, "the log must exceed several chunks"
    for n in (1, 2, 3, 10, 137):
        got = _tail_lines(path, n)
        assert len(got) == n, f"asked for {n} lines, got {len(got)}"
        assert got == forward[-n:], f"disagreement at n={n}"


def test_a_partial_line_at_the_chunk_edge_is_discarded(tmp_path):
    """The first line in the buffer is almost always a fragment. Kept, it is
    truncated JSON — so this is not a tidiness point, it is whether the caller
    gets an entry or a ValueError."""
    ch = _chain(tmp_path, 1500, pad=500)
    for line in _tail_lines(tmp_path / "chain.jsonl", 5):
        json.loads(line)                          # raises on a fragment


def test_multibyte_text_across_a_chunk_edge_in_a_large_log(tmp_path):
    """A chunk boundary landing inside a multi-byte character would raise
    UnicodeDecodeError. The cut at a newline makes that impossible; drive it
    on a log big enough for the read to stop mid-file."""
    ch = AuditChain(str(tmp_path / "chain.jsonl"))
    for i in range(1200):
        ch.append(event_type="SEED",
                  payload={"i": i, "note": "利益 · прибыль " + "é" * 400})
    got = ch.get_entries(limit=4)
    assert len(got) == 4
    assert "прибыль" in got[-1].payload["note"]


# ── the chunk boundary itself ────────────────────────────────────────
#
# With the real 64KB step, ONE read grabs ~128 lines, so the loop almost never
# iterates and the interesting cases — a buffer whose newline count lands
# exactly on `n`, a step edge falling inside a multi-byte character — are
# unreachable. A mutation pass showed it: deleting the partial-line cut and an
# off-by-one in the loop bound both survived every test above, because neither
# can be observed when the first read already overshoots by 125 lines.
#
# Shrinking the step makes the boundary reachable on purpose rather than by
# luck. It is the same helper either way — only how often it iterates changes.

@pytest.fixture
def tiny_chunks(monkeypatch):
    import bot.utils.audit_chain as ac
    monkeypatch.setattr(ac, "_TAIL_CHUNK", 64)


@pytest.mark.parametrize("n", [1, 2, 3, 5, 9, 20])
def test_the_tail_is_exact_when_the_read_iterates(tmp_path, tiny_chunks, n):
    """Every step now lands mid-line. Asking for n lines must return n — the
    off-by-one returns n-1 here, and cannot be seen at all with a 64KB step."""
    ch = _chain(tmp_path, 40, pad=200)
    path = tmp_path / "chain.jsonl"
    forward = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    got = _tail_lines(path, n)
    assert len(got) == n, f"asked for {n}, got {len(got)}"
    assert got == forward[-n:]


def test_every_returned_line_is_whole(tmp_path, tiny_chunks):
    """A kept fragment is truncated JSON, so this is not tidiness — it is
    whether the caller gets an entry or a ValueError."""
    ch = _chain(tmp_path, 30, pad=200)
    for line in _tail_lines(tmp_path / "chain.jsonl", 4):
        json.loads(line)


def test_a_step_edge_inside_a_multibyte_character(tmp_path, tiny_chunks):
    """`é` is two bytes; with a 64-byte step, edges land inside one constantly.
    Decoding the raw buffer would raise UnicodeDecodeError. Cutting at a
    newline — one ASCII byte — makes the slice start on a character boundary
    by construction."""
    ch = AuditChain(str(tmp_path / "chain.jsonl"))
    for i in range(25):
        ch.append(event_type="SEED", payload={"i": i, "note": "é" * 60 + " прибыль"})
    got = ch.get_entries(limit=6)
    assert len(got) == 6
    assert got[-1].payload["note"].endswith("прибыль")
    assert [e.sequence for e in got] == list(range(19, 25))


def test_a_step_edge_inside_a_character_by_construction(tmp_path, tiny_chunks):
    """The multi-byte test above is probabilistic: whether a 64-byte edge
    splits an `é` depends on alignment, and it happened to align. A mutation
    pass caught that — deleting the partial-line cut survived it.

    So this lays the bytes out by hand. Two 202-byte lines, each one ASCII
    character followed by a hundred `é`. Reading backwards in 64-byte steps
    from 404 lands at 340, 276, 212, 148 — every one of them an ODD offset
    into an `é` run, so decoding the raw buffer raises UnicodeDecodeError.
    Cutting at the newline first moves the start to byte 202, a boundary by
    construction.

    Written as raw bytes rather than through AuditChain because the point is
    the exact layout, and an appender that pads or reorders would silently
    turn this back into a coin flip.
    """
    path = tmp_path / "raw.jsonl"
    line_a = ("a" + "é" * 100).encode("utf-8")
    line_b = ("b" + "é" * 100).encode("utf-8")
    assert len(line_a) == 201, "the layout this test reasons about changed"
    path.write_bytes(line_a + b"\n" + line_b + b"\n")
    assert path.stat().st_size == 404

    got = _tail_lines(path, 1)
    assert got == ["b" + "é" * 100]

    # And the premise: the naive slice really is undecodable.
    with pytest.raises(UnicodeDecodeError):
        path.read_bytes()[148:].decode("utf-8")


def test_a_log_shorter_than_one_step(tmp_path, tiny_chunks):
    """The other edge: the read reaches position 0, where there is no partial
    line to discard and the first line must be KEPT."""
    ch = _chain(tmp_path, 1, pad=0)
    got = _tail_lines(tmp_path / "chain.jsonl", 5)
    assert len(got) == 1, "the only entry in the log was discarded as a fragment"
    assert json.loads(got[0])["sequence"] == 0


def test_asking_for_more_lines_than_exist(tmp_path, tiny_chunks):
    ch = _chain(tmp_path, 4, pad=100)
    got = _tail_lines(tmp_path / "chain.jsonl", 99)
    assert len(got) == 4


def test_get_entries_cost_does_not_grow_with_chain_length(tmp_path):
    """`append()` reaches get_entries every 50th entry through
    sign_latest_batch, so a full scan here is a second O(n) cost on the same
    hot path — one the append-level timing is too coarse to see, since only
    every 50th append pays it."""
    small = _chain(tmp_path / "s", 300, pad=300)
    large = _chain(tmp_path / "l", 6000, pad=300)

    def cost(ch):
        t0 = time.perf_counter()
        for _ in range(30):
            ch.get_entries(limit=10)
        return (time.perf_counter() - t0) / 30

    a, b = cost(small), cost(large)
    assert b < a * 5, (
        f"get_entries grew {b / a:.1f}x between a 300-entry chain and a "
        f"6 000-entry one — it is parsing the whole log to return ten lines")


# ── and only then, the cost ──────────────────────────────────────────

def _per_append(tmp_path, pre, appends=40):
    ch = _chain(tmp_path, pre, pad=200)
    t0 = time.perf_counter()
    for i in range(appends):
        ch.append(event_type="CLOSE", payload={"i": i, "pad": "x" * 200})
    return (time.perf_counter() - t0) / appends


def test_append_cost_does_not_grow_with_chain_length(tmp_path):
    """The finding, as an assertion.

    Ratio rather than an absolute budget: CI machines vary by more than the
    thing being measured, and a wall-clock threshold would either flake or be
    so loose it stopped detecting a return to O(n). The shape is what matters
    — 6 000 entries must not cost meaningfully more per append than 200.

    On the pre-fix implementation this ratio was ~17x.
    """
    small = _per_append(tmp_path / "s", 200)
    large = _per_append(tmp_path / "l", 6000)
    assert large < small * 5, (
        f"append cost grew {large / small:.1f}x between a 200-entry chain "
        f"({small * 1000:.2f} ms) and a 6 000-entry one ({large * 1000:.2f} ms) "
        f"— the tail read is scanning the whole log again")


def test_a_long_chain_can_be_built_at_all(tmp_path):
    """Quadratic seeding is why nobody ever measured a long chain: 20 000
    entries did not finish in two minutes. This is a smoke test with a
    generous ceiling — it fails on O(n²), not on a slow machine."""
    t0 = time.perf_counter()
    ch = _chain(tmp_path, 8000, pad=100)
    elapsed = time.perf_counter() - t0
    assert ch.get_chain_length() == 8000
    assert elapsed < 60, (
        f"building an 8 000-entry chain took {elapsed:.0f}s — appending is "
        f"scanning the whole file, which makes filling it quadratic")
