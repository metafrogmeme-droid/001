"""The export/convert stale-checkpoint guards — the 8B-shipped-as-3B defect.

Four consecutive exports (30-7 and 9-8-2026) merged a stale June 8B adapter
because ollama/export_model.py selected checkpoints from a hardcoded priority
list that put the 8B dirs first and did not contain the fresh run's dir at
all; ollama/convert_to_gguf.py then converted the stale merge with no
questions asked, and the 8B GGUF was pushed to the public registry under the
3B model's name. These tests pin the three properties that break that chain:
selection is by newest adapter mtime (a name can lie, an mtime cannot),
unreadable is never a guess (missing adapter_config.json refuses, it does not
default), and conversion is fail-closed (no manifest / stale merge / wrong
size each refuse on their own).
"""

import importlib.util
import json
import os
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, _ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


em = _load("export_model", "ollama/export_model.py")
cg = _load("convert_to_gguf", "ollama/convert_to_gguf.py")


def _mk_checkpoint(root, rel, base, age_days):
    """Plant a checkpoint dir with adapter weights aged `age_days` back."""
    d = root / rel
    d.mkdir(parents=True)
    if base is not None:
        (d / "adapter_config.json").write_text(
            json.dumps({"base_model_name_or_path": base}))
    weights = d / "adapter_model.safetensors"
    weights.write_bytes(b"weights")
    ts = time.time() - age_days * 86400
    os.utime(weights, (ts, ts))
    return d


# ── Selection: newest adapter wins, whatever the folder is named ─────


def test_newest_adapter_beats_the_old_priority_order(tmp_path):
    # The exact production layout that failed four times: an old 8B dir whose
    # NAME was first in the old priority list, and a fresh 3B dir whose name
    # the old list never searched.
    _mk_checkpoint(tmp_path, "runeclaw-8b-max-checkpoints/final-adapter",
                   "unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit", age_days=60)
    fresh = _mk_checkpoint(tmp_path, "runeclaw-3b-checkpoints/checkpoint-38152",
                           "unsloth/Llama-3.2-3B-Instruct-bnb-4bit", age_days=0)

    found = em.discover_checkpoints(str(tmp_path))
    assert found, "fresh checkpoint dir was not discovered at all"
    assert found[0]["path"] == os.path.normpath(str(fresh))
    assert "3B" in found[0]["base"]


def test_dir_without_adapter_weights_is_not_a_checkpoint(tmp_path):
    empty = tmp_path / "runeclaw-3b-checkpoints" / "checkpoint-100"
    empty.mkdir(parents=True)
    (empty / "adapter_config.json").write_text("{}")
    assert em.discover_checkpoints(str(tmp_path)) == []


def test_missing_adapter_config_is_unknown_not_a_default(tmp_path):
    # The old code defaulted a missing config to the 3B base — unreadable
    # rendered as a confident guess. It must surface as None instead.
    ckpt = _mk_checkpoint(tmp_path, "runeclaw-checkpoints/checkpoint-5",
                          base=None, age_days=0)
    assert em.read_adapter_base(str(ckpt)) is None
    found = em.discover_checkpoints(str(tmp_path))
    assert found[0]["base"] is None


def test_expect_base_gate():
    assert em.base_matches("unsloth/Llama-3.2-3B-Instruct-bnb-4bit", "3B")
    assert not em.base_matches("unsloth/Meta-Llama-3.1-8B-Instruct", "3B")
    # Unknown base never satisfies a gate.
    assert not em.base_matches(None, "3B")


def test_param_count_must_agree_with_base_name():
    three_b, eight_b = 3.21e9, 8.03e9
    assert em.params_consistent_with_base(three_b, "unsloth/Llama-3.2-3B-Instruct")
    assert not em.params_consistent_with_base(eight_b, "unsloth/Llama-3.2-3B-Instruct")
    assert em.params_consistent_with_base(eight_b, "unsloth/Meta-Llama-3.1-8B-Instruct")
    # A base whose name claims no size makes no claim to check.
    assert em.params_consistent_with_base(three_b, "some/custom-model")


# ── Conversion: fail-closed on unprovable or stale input ─────────────


def _mk_merged(root, param_count, adapter_mtime, with_manifest=True):
    merged = root / "runeclaw-model-merged"
    merged.mkdir()
    total_bytes = int(param_count * 2)
    (merged / "model.safetensors.index.json").write_text(
        json.dumps({"metadata": {"total_size": total_bytes}, "weight_map": {}}))
    if with_manifest:
        (merged / cg.MANIFEST_NAME).write_text(json.dumps({
            "checkpoint": "runeclaw-3b-checkpoints/checkpoint-38152",
            "adapter_mtime": adapter_mtime,
            "base_model": "unsloth/Llama-3.2-3B-Instruct",
            "param_count": param_count,
        }))
    return merged


def test_convert_refuses_a_merge_with_no_manifest(tmp_path):
    merged = _mk_merged(tmp_path, 3_210_000_000, time.time(), with_manifest=False)
    reason = cg.stale_reason(str(merged), str(tmp_path))
    assert reason is not None and "manifest" in reason.lower()


def test_convert_refuses_a_merge_older_than_the_newest_checkpoint(tmp_path):
    # The exact failure: fresh training on disk, merge built from June.
    _mk_checkpoint(tmp_path, "runeclaw-3b-checkpoints/checkpoint-38152",
                   "unsloth/Llama-3.2-3B-Instruct-bnb-4bit", age_days=0)
    stale_mtime = time.time() - 40 * 86400
    merged = _mk_merged(tmp_path, 8_030_000_000, stale_mtime)
    reason = cg.stale_reason(str(merged), str(tmp_path))
    assert reason is not None and "stale" in reason.lower()


def test_convert_accepts_a_fresh_matching_merge(tmp_path):
    ckpt = _mk_checkpoint(tmp_path, "runeclaw-3b-checkpoints/checkpoint-38152",
                          "unsloth/Llama-3.2-3B-Instruct-bnb-4bit", age_days=1)
    adapter_mtime = os.path.getmtime(ckpt / "adapter_model.safetensors")
    merged = _mk_merged(tmp_path, 3_210_000_000, adapter_mtime)
    assert cg.stale_reason(str(merged), str(tmp_path)) is None


def test_convert_refuses_weights_that_contradict_the_manifest(tmp_path):
    # Manifest says 3B, folder holds 8B-sized weights: modified after export.
    merged = _mk_merged(tmp_path, 3_210_000_000, time.time())
    (merged / "model.safetensors.index.json").write_text(
        json.dumps({"metadata": {"total_size": int(8.03e9 * 2)}, "weight_map": {}}))
    reason = cg.stale_reason(str(merged), str(tmp_path))
    assert reason is not None and "match" in reason.lower()


def test_unreadable_param_count_is_none_not_zero(tmp_path):
    empty = tmp_path / "merged"
    empty.mkdir()
    assert cg.detect_params_from_merged(str(empty)) is None


def test_gguf_size_gate_catches_the_8b_masquerade():
    three_b = 3_210_000_000
    q4_of_3b = int(three_b * 0.62)
    q4_of_8b = 4_805_409 * 1024  # the actual file that shipped, twice
    assert cg.gguf_size_ok(three_b, q4_of_3b, quantized=True)
    assert not cg.gguf_size_ok(three_b, q4_of_8b, quantized=True)
    # F16 of 3B ≈ 6.4 GB
    assert cg.gguf_size_ok(three_b, int(three_b * 2.0), quantized=False)
    assert not cg.gguf_size_ok(three_b, q4_of_3b, quantized=False)
